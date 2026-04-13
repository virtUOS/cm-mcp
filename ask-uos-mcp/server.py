import sys
sys.path.append("/cm-mcp")
import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any, List
from starlette.responses import JSONResponse
from fastmcp.server.event_store import EventStore
from config.models import RetrievalResult, ScrapeResult
import aiohttp
async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "mcp-server"})
import asyncpg
import yaml
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import BaseModel, Field
from tools.web_sarch_tool import async_search, crawl_urls_via_api, generate_summary
#from app_auth import auth
load_dotenv()
from log_conf.logger_setup import get_logger
from db.redis_pool import redis_client
log =get_logger()


@asynccontextmanager
async def lifespan(server: FastMCP):
    # Startup
    print("Initializing resources...")
    await redis_client.initialize()
    try:
        yield {}  # Pass context as a dict (accessible via server.request_context)
    finally:
        # Shutdown
        print("Cleaning up resources...")
        await redis_client.cleanup()


mcp = FastMCP("ask-uos-mcp", lifespan=lifespan)

@mcp.tool()
async def university_web_search(search_query:str)-> RetrievalResult:
    """
    This executes a google search within the Domain of the University. Thus, tool assists with answering queries related to the University of Osnabrück, including for example:

        - **Application Process:** Detailed steps, required documents, eligibility criteria, and timelines.
        - **Study Programs:** Information on faculties, degree programs, course structures, and tuition fees.
        - **Key Dates & Deadlines:** Up‑to‑date application periods, enrollment windows, exam schedules, and other relevant events.
        - **Contact Information:** Current phone numbers, email addresses, and office locations for admissions, student services, and specific departments.
        - **General question** Find General Infomrmation about the university

        **Usage Tips**

        - Leverage the chat history to maintain context across multiple questions.
        - Reference previous user interactions to provide personalized and coherent responses.
        - Verify that any date‑specific information reflects the latest updates from the university’s official sources.

        Args: 
            search_query: A query used by the search engine in order to find the information needed to answer the users question. 
    """

    result = await async_search(query=search_query)

    return result

@mcp.tool()
async def scrape_urls(urls:List) ->List[ScrapeResult]:
    """
    Scrapes multiple URLs and extracts their content, metadata, and links.
    
    Converts web content to clean markdown format with YAML frontmatter containing
    title, description, keywords, author, and source URL. Handles failures gracefully
    by providing available fallback content (cleaned HTML or raw HTML).
    
    Args:
        urls: List of URLs to scrape (HTTP/HTTPS)
    
    Returns:
        List[ScrapeResult]: Results with formatted_markdown (preferred), plus raw
                           HTML, cleaned HTML, and extracted metadata (title,
                           description, keywords, author, links)
    
    Example:
        results = await scrape_urls(["https://example.com"])
        print(results[0].formatted_markdown)
    """
    log.info("Tool call: scrape_urls")
    async with aiohttp.ClientSession() as session:
        result = await crawl_urls_via_api(urls=urls, session=session)
    return result


@mcp.tool()
async def summarize_content(text: str, query: str) -> str:

    """
    Generates a concise, query-focused summary of provided text content.
    
    This tool generates summaries. Summaries are optimized
    to address a specific question or query, filtering out irrelevant information
    while preserving critical elements like links, tables, and code blocks.
    
    Args:
        text: The full text content to summarize 
        query: A specific question or topic focus (string) that directs the
               summarization. The summary will emphasize information that
               directly addresses this query. 
    
    Returns:
        str: A markdown-formatted summary containing.

        Returns error message if summarization fails.
    
    Example:
        summary = await summarize_content(
            text="Long article about Python decorators...",
            query="What are the benefits of using decorators?"
        )
        # Returns: Concise markdown summary focusing on decorator benefits
    
    Use Cases:
        - Extracting key information from lengthy documents
        - Creating query-focused summaries for research
        - Condensing web scraping results
        - Generating executive summaries
        - Filtering relevant content from large texts
    
    """
    log.info("Tool call: summarize_content")
    result = await generate_summary(text, query)
    return result


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "ask-mcp-server"})


# Configure with EventStore for resumability
event_store = EventStore()

# Create ASGI application
app = mcp.http_app(
    event_store=event_store,
    retry_interval=2000, ) # Client reconnects after 2 seconds

# if __name__ == "__main__":
#     # TODO use supervisor to server the other mcps
#     import uvicorn
#     # server is accessible at the same URL: http://localhost:8001/mcp
#     uvicorn.run("server:app",  host="0.0.0.0", port=8001, log_level="info")
