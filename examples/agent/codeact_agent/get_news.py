# -*- coding: utf-8 -*-
"""The get_news tool in agentscope."""
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List

from pydantic import BaseModel, Field

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock


class NewsArticle(BaseModel):
    title: str = Field(description="Article title")
    source: str = Field(description="News source")
    pub_date: str = Field(description="Publication date")


class NewsOutput(BaseModel):
    query: str = Field(description="The search query used")
    articles: List[NewsArticle] = Field(description="List of news articles")


def get_news(query: str) -> ToolResponse:
    """Search for recent news articles matching a query.

    Args:
        query (`str`):
            The search query, e.g. "AI", "technology".

    Returns:
        `ToolResponse`:
            The tool response containing the news articles or an error
            message.
    """
    try:
        url = (
            "https://news.google.com/rss/search"
            f"?q={urllib.parse.quote(query)}"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")

        root = ET.fromstring(data)
        items = root.findall(".//item")

        articles = [
            NewsArticle(
                title=item.findtext("title", "No title"),
                source=item.findtext("source", "Unknown"),
                pub_date=item.findtext("pubDate", ""),
            )
            for item in items[:5]
        ]

        output = NewsOutput(query=query, articles=articles)

        lines = [f"News for '{query}':\n"]
        for i, article in enumerate(articles, 1):
            lines.append(f"{i}. {article.title} — {article.source} ({article.pub_date})")
        if len(articles) == 0:
            lines.append("No articles found.")

        return ToolResponse(
            content=[TextBlock(type="text", text="\n".join(lines))],
            metadata=output.model_dump(),
        )
    except urllib.error.URLError as e:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {e}")],
        )
    except ET.ParseError as e:
        return ToolResponse(
            content=[
                TextBlock(type="text", text=f"Error: Failed to parse news data: {e}"),
            ],
        )
