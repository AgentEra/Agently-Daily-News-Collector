from __future__ import annotations

import os
from typing import Any

from tavily import AsyncTavilyClient

from news_collector.config import AppSettings, SearchNewsTimeLimit

from .base import SearchToolProtocol

_TIMELIMIT_TO_DAYS: dict[str, int] = {"d": 1, "w": 7, "m": 30}


class TavilySearchTool(SearchToolProtocol):
    def __init__(self, settings: AppSettings):
        api_key = os.getenv("TAVILY_API_KEY", "")
        self._client = AsyncTavilyClient(api_key=api_key)

    async def search_news(
        self,
        *,
        query: str,
        timelimit: SearchNewsTimeLimit,
        max_results: int,
    ) -> list[dict[str, Any]]:
        days = _TIMELIMIT_TO_DAYS.get(timelimit, 1)
        response = await self._client.search(
            query=query,
            topic="news",
            max_results=max_results,
            days=days,
        )
        results: list[dict[str, Any]] = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "body": item.get("content", ""),
                "date": item.get("published_date", ""),
            })
        return results
