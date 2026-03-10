from __future__ import annotations

from typing import Any, Protocol

from news_collector.config import SearchNewsTimeLimit


class SearchToolProtocol(Protocol):
    async def search_news(
        self,
        *,
        query: str,
        timelimit: SearchNewsTimeLimit,
        max_results: int,
    ) -> list[dict[str, Any]]:
        ...


class BrowseToolProtocol(Protocol):
    async def browse(self, url: str) -> str:
        ...
