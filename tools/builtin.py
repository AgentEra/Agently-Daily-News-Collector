from __future__ import annotations

from typing import Any

from agently.builtins.tools import Browse, Search
from ddgs.exceptions import DDGSException

from news_collector.config import AppSettings, SearchNewsTimeLimit

from .base import BrowseToolProtocol, SearchToolProtocol


class AgentlyBuiltinSearchTool(SearchToolProtocol):
    def __init__(self, settings: AppSettings):
        self._tool = Search(
            proxy=settings.search.proxy or settings.proxy,
            region=settings.search.region,
            backend=settings.search.backend,
        )

    async def search_news(
        self,
        *,
        query: str,
        timelimit: SearchNewsTimeLimit,
        max_results: int,
    ) -> list[dict[str, Any]]:
        try:
            results = await self._tool.search_news(
                query=query,
                timelimit=timelimit,
                max_results=max_results,
            )
        except DDGSException as exc:
            if "No results found" in str(exc):
                return []
            raise
        return results if isinstance(results, list) else []


class AgentlyBuiltinBrowseTool(BrowseToolProtocol):
    def __init__(self, settings: AppSettings):
        self._tool = Browse(
            proxy=settings.browse.proxy or settings.proxy,
            enable_pyautogui=False,
            enable_playwright=settings.browse.enable_playwright,
            enable_bs4=True,
            response_mode=settings.browse.response_mode,
            max_content_length=settings.browse.max_content_length,
            min_content_length=settings.browse.min_content_length,
            playwright_headless=settings.browse.playwright_headless,
        )

    async def browse(self, url: str) -> str:
        result = await self._tool.browse(url)
        return str(result or "")


def create_search_tool(settings: AppSettings) -> SearchToolProtocol:
    return AgentlyBuiltinSearchTool(settings)


def create_browse_tool(settings: AppSettings) -> BrowseToolProtocol:
    return AgentlyBuiltinBrowseTool(settings)
