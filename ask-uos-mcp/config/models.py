import json
from typing import Optional
from enum import Enum
from pydantic import BaseModel, Field
from log_conf.logger_setup import get_logger
logger =get_logger()



class ScrapeResult(BaseModel):
    url: Optional[str] = None
    html: Optional[str] = None
    cleaned_html: Optional[str] = None
    links: Optional[dict] = None
    markdown: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    formatted_markdown: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None

    def model_post_init(self, _context):
        if self.markdown:
            self.formatted_markdown = self._formatted_markdown()
        elif self.cleaned_html:
            self.formatted_markdown = self.cleaned_html
            logger.error(
                f"Fail to format scraped content, model is being provided html str"
            )
        elif self.html:
            self.formatted_markdown = self.html
            logger.error(
                f"Fail to format scraped content, model is being provided html str"
            )
        else:
            self.formatted_markdown = "No content found"
            logger.error(f"Crawler failed to retrieve content")

    def _formatted_markdown(self) -> str:
        md_content = f"""
---
title: "{self.title or ''}"
url: "{self.url}"
description: "{self.description or ''}"
keywords: "{self.keywords or ''}"
author: "{self.author or ''}"
---

#### Information taken from: {self.url}\n\n{self.markdown}
"""
        return md_content

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "RetrievalResult":
        return cls.model_validate_json(data)


class Reference(BaseModel):
    source: str
    page: int | None = None
    doc_id: str | None = None
    # TODO Delete once metadata is added to RAGFlow API (user to reference FAQ source)
    url_reference_askuos: str | None = None


class RetrievalResult(BaseModel):
    result_text: str = Field(description="Retrieved text from a tool", default="")
    reference: list = []
    source_name: str = Field(
        description="Name of the source or collection where the text was retrieved from",
        default="",
    )
    search_query: str

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "RetrievalResult":
        return cls.model_validate_json(data)


class CrawlSettings(BaseModel):
    """Settings for web crawler behavior"""

    base_url: str
    crawl_payload: dict  # TODO : requires special validation, use the crawl4ai schema
    ttl_redis: int

class AppConfig(BaseModel):
    # summarize if context is >= summary_threshold
    summary_threshold: int



class ProviderNames(str, Enum):
    OPENAI = "openai"
    GOOGLE = "google"
    SELF_HOSTED = "self-hosted"


class RoleNames(str, Enum):
    MAIN = "main"
    HELPER = "helper"

class Model(BaseModel):
    """
    Configuration for the model being used.
    """

    provider: ProviderNames
    role: RoleNames
    model_name: str
    base_url:Optional[str]



