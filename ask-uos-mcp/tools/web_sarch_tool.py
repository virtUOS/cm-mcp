import copy
import os
import sys

sys.path.append("/cm-mcp")

import asyncio
from typing import List, Optional, Tuple

import aiohttp
import dotenv
import redis.asyncio as redis

from config.models import RetrievalResult, ScrapeResult
from db.redis_pool import redis_client
from exceptions import ProgrammableSearchException
from utils.tool_helpers import decode_string
from utils.tool_helpers import compute_search_num_tokens
from utils.tool_helpers import model_registry

from log_conf.logger_setup import get_logger
logger =get_logger()

from config.core_config import settings

# colorama.init(strip=True)
dotenv.load_dotenv()

# Application context URLs
APPLICATION_CONTEXT_URLS = [
    # "https://www.uni-osnabrueck.de/studieren/bewerbung-und-studienstart/bewerbung-zulassung-und-einschreibung/zulassungsbeschraenkungen",
    # "https://www.uni-osnabrueck.de/studieren/bewerbung-und-studienstart/bewerbung-zulassung-und-einschreibung",
]

SEARCH_URL = os.getenv("SEARCH_URL")
# TODO Increase the number of websites to visit once cache is improved
MAX_NUM_LINKS = 6

# TODO Change cache mechanism to enabled (in config.yml)
CRAWL_API_URL = settings.crawl_settings.base_url
CRAWL_PAYLOAD = settings.crawl_settings.crawl_payload
TTL = settings.crawl_settings.ttl_redis

no_content_found_message = "Content not found"


async def generate_summary(text: str, query: str) -> str:
    """Generate a summary of the provided text."""
    logger.info(f"[LMM-OPERATION] Summarizing content, query: {query}")

    reduce_template_string = f"""Your task it to create a concise summary of the text provided. 
## Instruction: Your task is to generate a concise and accurate summary of the provided text. The summary should effectively capture the key points and concepts while strictly avoiding any interpretations or subjective additions.
1. Focus on Relevance: Emphasize information that directly addresses the question/query specified below.
2. Handling External Sources: Do not condense or modify links/urls or references to external sources; include them as they appear in the original text.
3. Tables and Data: If the text includes tables, avoid summarizing their contents. Instead, include them in their entirety within the summary.
4. Language Consistency: Ensure the summary is written in the same language as the original text.
4. Formatting: Present the summary in markdown format, which should encompass all necessary elements, including tables and code blocks, without alterations.

    Summarize this text:
    {text}

    question/query: {query}
    
    """

    # TODO :BUG CANNOT CHANGE GLOBAL VARIABLE, MY RAISE A RACE CONDITION
    settings.llm_summarization_mode = True

    messages = [("human", reduce_template_string)]
    # TODO: Allthough is very unlikely, make sure that the messages length is not greater than llm context window
    try:
        response = model_registry.llm_optional.llm.invoke(messages)
        summary = response.content
    except:
        logger.error(f"[WEB-SEARCH-SUMMARY] Error while summarizing web content")
        return "Error while summarizing web content"
    return summary



def compute_tokens(search_result_text: str, query: str) -> Tuple[int, int]:
    """Compute tokens for the search result text."""
    current_search_num_tokens = compute_search_num_tokens(search_result_text + query)
    # total_tokens = internal_num_tokens + current_search_num_tokens
    total_tokens = current_search_num_tokens
    return total_tokens, current_search_num_tokens


async def crawl_urls_via_api(
    urls: List[str],
    session: aiohttp.ClientSession,
    crawl_payload: Optional[dict] = None,
) -> List[ScrapeResult]:
    """
    Crawl multiple URLs using the API endpoint.
    Returns a list of crawl results.
    """

    try:
        scraped_results = []
        payload = copy.deepcopy(crawl_payload or CRAWL_PAYLOAD)
        payload["urls"] = urls
        payload["crawler_config"]["params"][
            "stream"
        ] = False  # ensure non-streaming mode

        async with session.post(
            CRAWL_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status != 200:
                logger.error(
                    "[CRAWL] API returned non-200 status: %d, body: %s",
                    response.status,
                    await response.text(),
                )
                return []

                # Wait for the complete response
            response_data = await response.json()

            # Extract results from the response
            if response_data.get("success") is False:
                logger.error("[CRAWL] Crawl API reported failure")
                return []

            results_data: List[dict] = response_data.get("results", [])

            for result_data in results_data:
                scraped_results.append(
                    ScrapeResult(
                        url=result_data["url"],
                        html=result_data["html"],
                        cleaned_html=result_data["cleaned_html"],
                        markdown=result_data["markdown"]["raw_markdown"],
                        title=result_data["metadata"]["title"],
                        description=result_data["metadata"]["description"],
                        keywords=result_data["metadata"]["keywords"],
                        author=result_data["metadata"]["author"],
                        # links=result_data["links"],
                    )
                )
            return scraped_results
    except Exception as e:
        logger.error(f"[CRAWL]Exception while crawling via API: {e}")
        return []


async def extract_url_redis(
    url: str, cache_key: str, client: redis.Redis
) -> ScrapeResult | str:

    # Try to get from cache
    cached_content = await client.get(cache_key)
    if cached_content:
        logger.debug("[REDIS] CACHE HIT – key=%s (url=%s)", cache_key, url)
        return ScrapeResult.from_json(cached_content)

    logger.debug("[REDIS] CACHE MISS – key=%s (url=%s)", cache_key, url)
    return url


async def _google_search(session: aiohttp.ClientSession, url: str):
    """
    Coroutine that runs a Google Programmable Search
    """
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"Search failed, status={response.status}")

        data = await response.json()
        total = int(data.get("searchInformation", {}).get("totalResults", 0))

        if total > 0:
            links = [item["link"] for item in data["items"]]
            logger.debug("Search returned %d links", len(links))
            return links
        else:
            logger.warning("[GOOGLE] No search results.")
            return []


async def visit_urls_extract(
    url: str,  # search url
    query: str,
    max_num_links: int = MAX_NUM_LINKS,
    do_not_visit_links: List = [],
    client: redis.Redis = None,
) -> Tuple[List, List]:
    """Visit URLs and extract content."""

    contents = []
    links_search = []
    scraping_result = []
    filtered_urls = []
    cache_key_prefix = f"{__name__}:visit_urls_extract:"
    cache_tasks = []
    async with aiohttp.ClientSession() as session:

        links_search = await _google_search(session, url)
        if not links_search:
            return [], []

        urls = []
        for href in links_search:
            # Skip PDF files
            if href.endswith(".pdf"):
                continue

            if len(urls) >= max_num_links:
                break

            # Skip already visited links
            if href in urls or href in do_not_visit_links:
                continue

            urls.append(href)

        # ------------------------------------------------------------------
        # Cache lookup
        # ------------------------------------------------------------------
        task_url_cache = [
            extract_url_redis(url=u, cache_key=f"{cache_key_prefix}{u}", client=client)
            for u in urls
        ]
        task_url_cache_result = await asyncio.gather(
            *task_url_cache, return_exceptions=True
        )
        # ------------------------------------------------------------------
        # Process cache results
        # ------------------------------------------------------------------
        for c in task_url_cache_result:
            if isinstance(c, str):
                filtered_urls.append(c)
            elif isinstance(c, ScrapeResult):
                contents.append(
                    c.formatted_markdown
                )  # ← use cached content immediately
            elif isinstance(c, Exception):
                logger.error(f"[REDIS] Error accessing cache: {c}")

        if filtered_urls:
            scraping_result = await crawl_urls_via_api(filtered_urls, session=session)
            # scraping_result.extend(freshly_scraped)
        for scraped in scraping_result:
            # result_url, result_content = await get_web_content(url, client)

            if scraped.formatted_markdown and len(scraped.formatted_markdown) >= 70:
                cache_key = f"{cache_key_prefix}{scraped.url}"
                cache_tasks.append(
                    asyncio.create_task(client.setex(cache_key, TTL, scraped.to_json()))
                )
                contents.append(scraped.formatted_markdown)

    # ------------------------------------------------------------------
    # Summarisation / token‑count handling
    # ------------------------------------------------------------------
    try:
        if contents:
            # Order the contents by the index
            contents = (
                sorted(contents, key=lambda x: x[1])
                if isinstance(contents[0], tuple)
                else contents
            )
            total_tokens, _ = compute_tokens("".join(contents), query)
            if total_tokens > settings.app.summary_threshold:
                for i in range(len(contents) - 1, -1, -1):
                    contents[i] = await generate_summary(contents[i], query)
                    # Update the total tokens
                    total_tokens, _ = compute_tokens("".join(contents), query)
                    if total_tokens <= settings.app.summary_threshold:
                        break
    finally:
        c_result = await asyncio.gather(*cache_tasks, return_exceptions=True)
        for cr in c_result:
            if isinstance(cr, Exception):
                logger.exception(
                    f"[REDIS] Error while caching content for URL: {cr}",
                )
    # print(await client.keys("*"))
    return urls, contents


async def async_search(**kwargs) -> RetrievalResult:
    """Asynchronous search function that encapsulates the search functionality."""

    try:

        client = redis_client.client
        logger.debug("[REDIS] Async client created: %s", client)
        query = kwargs.get("query", "")
        query_url = decode_string(query)
        url = SEARCH_URL + query_url
        # TODO: Needs to be implemented
        #do_not_visit_links = kwargs.get("do_not_visit_links", [])

        # -------------------------- cache lookup --------------------------
        cache_key = f"{__name__}:async_search:{url}"
        cached_content = await client.get(cache_key)
        if cached_content:
            logger.debug("[REDIS] Retrieved cached searched results (urls)")
            return RetrievalResult.from_json(cached_content)

        logger.debug("[SEARCH] Cache miss – proceeding with live search")

        visited_urls, contents = await visit_urls_extract(
            url=url,
            query=query,
            #do_not_visit_links=do_not_visit_links,
            client=client,
        )

        final_output = "\n".join(contents)

        if final_output:
            # For testing
            final_output_tokens, final_search_tokens = compute_tokens(
                final_output, query
            )
            logger.info(f"[SEARCH] Search tokens: {final_search_tokens}")
            logger.info(
                f"[SEARCH] Final output (search + prompt): {final_output_tokens}"
            )

        retrieved = RetrievalResult(
            result_text=final_output, reference=visited_urls, search_query=query
        )
        # -------------------------- cache store ---------------------------
        if len(final_output) > 20:
            await client.setex(cache_key, TTL, retrieved.to_json())

        return retrieved

    except redis.ConnectionError as e:
        logger.error(
            f"[REDIS] Connection error. It was not possible to establish a connection: {e}"
        )
        raise redis.ConnectionError("Redis Failed") from e
    except ProgrammableSearchException as e:
        raise ProgrammableSearchException(
            f"Failed: Programmable Search Engine. Status: {e}"
        )
    except Exception as e:
        logger.exception(f"[SEARCH] Error while searching the web: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    # Use for testing/debugging
    import asyncio

    import redis.asyncio as aioredis

    async def test():
        client = aioredis.Redis(host="redis", port=6379, decode_responses=True)
        await client.setex("test_key", 300, "hello")
        val = await client.get("test_key")
        print(f"Stored and retrieved: {val}")  # Should print "hello"
        await client.aclose()

    asyncio.run(test())
