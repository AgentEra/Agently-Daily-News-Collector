from __future__ import annotations

import copy
import re
from typing import Any, Callable

from agently import TriggerFlowRuntimeData

from .common import (
    DailyNewsChunkConfig,
    create_editor_agent,
    is_chinese_language,
    require_logger,
    require_search_tool,
    safe_int,
)
from .summary_chunks import pick_news


def create_search_column_news_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def search_column_news(data: TriggerFlowRuntimeData) -> dict[str, Any] | None:
        column_outline = data.value if isinstance(data.value, dict) else None
        if not isinstance(column_outline, dict):
            return None

        title = str(column_outline.get("column_title") or "").strip()
        if not title:
            return None

        logger = require_logger(data)
        request = _get_request_context(data)
        logger.info("[Start Generate Column] %s", title)
        try:
            searched_news = await search_news(
                config,
                logger,
                require_search_tool(data),
                column_outline,
                topic=str(request.get("topic") or ""),
            )
        except Exception as exc:
            logger.exception("[Column Search Failed] %s: %s", title, exc)
            return None

        logger.info("[Search News Count] %s => %s", title, len(searched_news))
        if not searched_news:
            return None
        return {
            "column_outline": copy.deepcopy(column_outline),
            "searched_news": searched_news,
        }

    return search_column_news


def create_pick_column_news_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def pick_column_news(data: TriggerFlowRuntimeData) -> dict[str, Any] | None:
        context = _coerce_column_context(data.value)
        if context is None:
            return None

        column_outline = context["column_outline"]
        searched_news = context["searched_news"]
        title = str(column_outline.get("column_title") or "").strip()
        logger = require_logger(data)
        try:
            picked_news = await pick_news(
                config,
                column_outline,
                searched_news,
            )
        except Exception as exc:
            logger.exception("[Column Pick Failed] %s: %s", title, exc)
            return None

        logger.info("[Picked News Count] %s => %s", title, len(picked_news))
        if not picked_news:
            return None
        return {
            **context,
            "picked_news": picked_news,
        }

    return pick_column_news


def create_write_column_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def write_column(data: TriggerFlowRuntimeData) -> dict[str, Any] | None:
        context = _coerce_column_context(data.value, require_picked=True, require_summarized=True)
        if context is None:
            return None

        column_outline = context["column_outline"]
        title = str(column_outline.get("column_title") or "").strip()
        logger = require_logger(data)
        try:
            column_result = await _write_column(
                config,
                column_outline,
                context["summarized_news"],
            )
        except Exception as exc:
            logger.exception("[Column Write Failed] %s: %s", title, exc)
            return None

        logger.info("[Column Ready] %s", title)
        return column_result

    return write_column


def _get_request_context(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = data.state.get("request")
    if not isinstance(request, dict):
        request = data.get_runtime_data("request")
    return request if isinstance(request, dict) else {}


def _coerce_column_context(
    value: Any,
    *,
    require_picked: bool = False,
    require_summarized: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    column_outline = value.get("column_outline")
    searched_news = value.get("searched_news")
    if not isinstance(column_outline, dict) or not isinstance(searched_news, list):
        return None

    context: dict[str, Any] = {
        "column_outline": column_outline,
        "searched_news": searched_news,
    }

    picked_news = value.get("picked_news")
    if picked_news is not None:
        if not isinstance(picked_news, list):
            return None
        context["picked_news"] = picked_news
    elif require_picked:
        return None

    summarized_news = value.get("summarized_news")
    if summarized_news is not None:
        if not isinstance(summarized_news, list):
            return None
        context["summarized_news"] = summarized_news
    elif require_summarized:
        return None

    return context


async def search_news(
    config: DailyNewsChunkConfig,
    logger,
    search_tool,
    column_outline: dict[str, Any],
    *,
    topic: str,
) -> list[dict[str, Any]]:
    query = str(column_outline.get("search_keywords") or "").strip()
    if not query:
        return []
    queries = build_search_queries(
        search_keywords=query,
        topic=topic,
    )
    normalized_results = []
    seen_urls: set[str] = set()
    for candidate in queries:
        try:
            raw_results = await search_tool.search_news(
                query=candidate,
                timelimit=config.settings.search.timelimit,
                max_results=config.settings.search.max_results,
            )
        except Exception as exc:
            logger.warning("[Search Failed] %s => %s", candidate, exc)
            continue

        added_count = 0
        for raw in raw_results or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or raw.get("href") or "").strip()
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            normalized_results.append(
                {
                    "id": len(normalized_results),
                    "title": title,
                    "brief": str(raw.get("body") or raw.get("snippet") or "").strip(),
                    "url": url,
                    "source": str(raw.get("source") or "").strip(),
                    "date": str(raw.get("date") or "").strip(),
                }
            )
            added_count += 1
            if len(normalized_results) >= config.settings.search.max_results:
                break
        logger.info("[Search Attempt] %s => %s", candidate, added_count)
        if len(normalized_results) >= config.settings.search.max_results:
            break
    return normalized_results


def build_search_queries(
    *,
    search_keywords: str,
    topic: str,
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        normalized = re.sub(r"\s+", " ", query).strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        queries.append(normalized)

    add(search_keywords)

    keyword_tokens = _extract_search_tokens(search_keywords)
    topic_tokens = _extract_search_tokens(topic)

    if keyword_tokens:
        add(" ".join(keyword_tokens))
        non_year_keyword_tokens = [token for token in keyword_tokens if not re.fullmatch(r"\d{4}", token)]
        if non_year_keyword_tokens:
            add(" ".join(non_year_keyword_tokens))

    if topic_tokens:
        add(" ".join(topic_tokens))
        add(" ".join([*topic_tokens, "news"]))

    merged_tokens = _dedupe_tokens([*topic_tokens, *keyword_tokens])
    if merged_tokens:
        add(" ".join(merged_tokens))
        add(" ".join([*merged_tokens, "news"]))

    return queries


def _extract_search_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*", text)
    return _dedupe_tokens(tokens)[:8]


def _dedupe_tokens(tokens: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = token.strip()
        lower_token = normalized.lower()
        if not normalized or lower_token in seen:
            continue
        seen.add(lower_token)
        result.append(normalized)
    return result


async def _write_column(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    summarized_news: list[dict[str, Any]],
) -> dict[str, Any]:
    slimmed_news = []
    for index, news in enumerate(summarized_news):
        slimmed_news.append(
            {
                "id": index,
                "title": news["title"],
                "summary": news["summary"],
                "url": news["url"],
                "source": news.get("source", ""),
                "date": news.get("date", ""),
                "recommend_comment": news.get("recommend_comment", ""),
            }
        )

    column_result = await (
        create_editor_agent(kind="column")
        .load_yaml_prompt(
            config.prompt_dir / "write_column.yaml",
            {
                "news_list": slimmed_news,
                "column_title": column_outline["column_title"],
                "column_requirement": column_outline["column_requirement"],
                "language": config.settings.workflow.output_language,
            },
        )
        .async_start(
            ensure_keys=[
                "prologue",
                "news_list[*].id",
                "news_list[*].recommend_comment",
            ]
        )
    )

    if not isinstance(column_result, dict):
        return _build_fallback_column(config, column_outline, summarized_news)

    final_news_list = []
    used_ids: set[int] = set()
    for item in column_result.get("news_list", []):
        if not isinstance(item, dict):
            continue
        news_id = safe_int(item.get("id"), -1)
        if news_id < 0 or news_id >= len(summarized_news) or news_id in used_ids:
            continue
        used_ids.add(news_id)
        final_item = copy.deepcopy(summarized_news[news_id])
        refined_comment = str(item.get("recommend_comment") or "").strip()
        if refined_comment:
            final_item["recommend_comment"] = refined_comment
        final_news_list.append(final_item)

    if not final_news_list:
        final_news_list = summarized_news[: config.settings.workflow.max_news_per_column]

    prologue = str(column_result.get("prologue") or "").strip()
    if not prologue:
        prologue = _build_fallback_prologue(config, column_outline, final_news_list)

    return {
        "title": column_outline["column_title"],
        "prologue": prologue,
        "news_list": final_news_list,
    }


def _build_fallback_column(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    summarized_news: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": column_outline["column_title"],
        "prologue": _build_fallback_prologue(config, column_outline, summarized_news),
        "news_list": summarized_news[: config.settings.workflow.max_news_per_column],
    }


def _build_fallback_prologue(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    news_list: list[dict[str, Any]],
) -> str:
    if not news_list:
        return str(column_outline.get("column_requirement") or "")

    if is_chinese_language(config.settings.workflow.output_language):
        lead_titles = "，".join(f"《{news['title']}》" for news in news_list[:3])
        return f"本栏目围绕“{column_outline['column_title']}”整理了以下重点内容：{lead_titles}。"

    lead_titles = ", ".join(news["title"] for news in news_list[:3])
    return f"This section highlights the most relevant stories for {column_outline['column_title']}: {lead_titles}."


__all__ = [
    "create_search_column_news_chunk",
    "create_pick_column_news_chunk",
    "create_write_column_chunk",
]
