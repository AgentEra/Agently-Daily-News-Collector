from __future__ import annotations

import copy
from typing import Any, Callable

from agently import TriggerFlowRuntimeData

from .common import (
    DailyNewsChunkConfig,
    create_editor_agent,
    is_chinese_language,
    require_browse_tool,
    require_logger,
    safe_int,
)


def create_prepare_summary_candidates_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def prepare_summary_candidates(data: TriggerFlowRuntimeData):
        context = _coerce_summary_context(data.value)
        if context is None:
            data.state.set("summary_context", None, emit=False)
            data.state.set("summary_candidates", [], emit=False)
            data.state.set("summary_cursor", 0, emit=False)
            data.state.set("summary_results", [], emit=False)
            data.state.set("summary_target_count", 0, emit=False)
            await data.async_emit("Summary.Done", None)
            return

        candidates = build_summary_candidates(
            config,
            context["column_outline"],
            context["searched_news"],
            context["picked_news"],
        )
        target_count = min(
            len(context["picked_news"]),
            config.settings.workflow.max_news_per_column,
        )

        data.state.set("summary_context", copy.deepcopy(context), emit=False)
        data.state.set("summary_candidates", candidates, emit=False)
        data.state.set("summary_cursor", 0, emit=False)
        data.state.set("summary_results", [], emit=False)
        data.state.set("summary_target_count", target_count, emit=False)

        if target_count <= 0 or not candidates:
            await data.async_emit("Summary.Done", None)
        else:
            await data.async_emit("Summary.Dispatch", None)

    return prepare_summary_candidates


def create_dispatch_summary_batch_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def dispatch_summary_batch(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
        candidates = data.state.get("summary_candidates") or []
        cursor = safe_int(data.state.get("summary_cursor"), 0)
        target_count = safe_int(data.state.get("summary_target_count"), 0)
        summary_results = data.state.get("summary_results") or []
        if not isinstance(candidates, list) or not isinstance(summary_results, list):
            raise RuntimeError("Invalid summary flow state.")

        remaining_needed = target_count - len(summary_results)
        batch_size = min(
            max(config.settings.workflow.summary_concurrency, 1),
            max(remaining_needed, 0),
            len(candidates) - cursor,
        )
        if batch_size <= 0:
            raise RuntimeError("Summary dispatch received no work. Summary.Done should have been emitted first.")

        batch = candidates[cursor : cursor + batch_size]
        data.state.set("summary_cursor", cursor + batch_size, emit=False)
        return batch

    return dispatch_summary_batch


def create_summarize_candidate_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def summarize_candidate(data: TriggerFlowRuntimeData) -> dict[str, Any]:
        candidate = data.value if isinstance(data.value, dict) else {}
        news = candidate.get("news")
        is_backup = bool(candidate.get("is_backup"))
        if not isinstance(news, dict):
            return {
                "news": {},
                "is_backup": is_backup,
                "summarized": None,
            }

        logger = require_logger(data)
        column_outline = _get_summary_column_outline(data)
        summarized = await summarize_single_news(
            config,
            logger,
            require_browse_tool(data),
            column_outline,
            news,
        )
        return {
            "news": copy.deepcopy(news),
            "is_backup": is_backup,
            "summarized": summarized,
        }

    return summarize_candidate


def create_merge_summary_batch_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def merge_summary_batch(data: TriggerFlowRuntimeData):
        logger = require_logger(data)
        results = data.value if isinstance(data.value, list) else []
        summary_results = data.state.get("summary_results") or []
        cursor = safe_int(data.state.get("summary_cursor"), 0)
        candidates = data.state.get("summary_candidates") or []
        target_count = safe_int(data.state.get("summary_target_count"), 0)

        if not isinstance(summary_results, list) or not isinstance(candidates, list):
            raise RuntimeError("Invalid summary merge state.")

        for item in results:
            if not isinstance(item, dict):
                continue
            news = item.get("news")
            summarized = item.get("summarized")
            is_backup = bool(item.get("is_backup"))
            title = str(news.get("title") or "").strip() if isinstance(news, dict) else ""

            if isinstance(summarized, dict):
                summary_results.append(summarized)
                continue
            if is_backup:
                logger.info("[Backup News Rejected] %s", title)
            elif cursor < len(candidates):
                logger.info("[Backup News Activated] %s", title)

        data.state.set("summary_results", summary_results, emit=False)
        if len(summary_results) >= target_count or cursor >= len(candidates):
            await data.async_emit("Summary.Done", None)
        else:
            await data.async_emit("Summary.Dispatch", None)

    return merge_summary_batch


def create_finalize_summary_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def finalize_summary(data: TriggerFlowRuntimeData) -> dict[str, Any]:
        context = data.state.get("summary_context")
        if not isinstance(context, dict):
            return {
                "column_outline": {},
                "searched_news": [],
                "picked_news": [],
                "summarized_news": [],
            }

        result = copy.deepcopy(context)
        summarized_news = data.state.get("summary_results") or []
        result["summarized_news"] = summarized_news if isinstance(summarized_news, list) else []
        logger = require_logger(data)
        title = str(result.get("column_outline", {}).get("column_title") or "").strip()
        logger.info("[Summarized News Count] %s => %s", title, len(result["summarized_news"]))
        return result

    return finalize_summary


def _coerce_summary_context(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    column_outline = value.get("column_outline")
    searched_news = value.get("searched_news")
    picked_news = value.get("picked_news")
    if not isinstance(column_outline, dict) or not isinstance(searched_news, list) or not isinstance(picked_news, list):
        return None

    return {
        "column_outline": copy.deepcopy(column_outline),
        "searched_news": copy.deepcopy(searched_news),
        "picked_news": copy.deepcopy(picked_news),
    }


def _get_summary_column_outline(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    context = data.state.get("summary_context")
    if isinstance(context, dict) and isinstance(context.get("column_outline"), dict):
        return context["column_outline"]
    return {}


def build_summary_candidates(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    searched_news: list[dict[str, Any]],
    picked_news: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    picked_urls = {
        str(news.get("url") or "").strip()
        for news in picked_news
        if str(news.get("url") or "").strip()
    }
    seen_urls: set[str] = set()

    for news in picked_news:
        url = str(news.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(
            {
                "news": copy.deepcopy(news),
                "is_backup": False,
            }
        )

    for news in searched_news:
        url = str(news.get("url") or "").strip()
        if not url or url in seen_urls or url in picked_urls:
            continue
        seen_urls.add(url)
        backup_news = copy.deepcopy(news)
        if not str(backup_news.get("recommend_comment") or "").strip():
            backup_news["recommend_comment"] = build_backup_recommend_comment(
                config,
                column_outline,
                backup_news,
            )
        candidates.append(
            {
                "news": backup_news,
                "is_backup": True,
            }
        )

    return candidates


async def pick_news(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    searched_news: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pick_results = await (
        create_editor_agent(kind="column")
        .load_yaml_prompt(
            config.prompt_dir / "pick_news.yaml",
            {
                "column_news": searched_news,
                "column_title": column_outline["column_title"],
                "column_requirement": column_outline["column_requirement"],
                "max_news_per_column": config.settings.workflow.max_news_per_column,
            },
        )
        .async_start(
            ensure_keys=[
                "[*].id",
                "[*].can_use",
                "[*].relevance_score",
                "[*].recommend_comment",
            ]
        )
    )

    if not isinstance(pick_results, list):
        return []

    picked_news = []
    seen_ids: set[int] = set()
    sorted_results = sorted(
        [item for item in pick_results if isinstance(item, dict)],
        key=lambda item: safe_int(item.get("relevance_score"), 0),
        reverse=True,
    )
    for item in sorted_results:
        if item.get("can_use") is not True:
            continue
        news_id = safe_int(item.get("id"), -1)
        if news_id < 0 or news_id >= len(searched_news) or news_id in seen_ids:
            continue
        seen_ids.add(news_id)
        picked_item = copy.deepcopy(searched_news[news_id])
        picked_item["recommend_comment"] = str(item.get("recommend_comment") or "").strip()
        picked_item["relevance_score"] = safe_int(item.get("relevance_score"), 0)
        picked_news.append(picked_item)
        if len(picked_news) >= config.settings.workflow.max_news_per_column:
            break
    return picked_news


async def summarize_single_news(
    config: DailyNewsChunkConfig,
    logger,
    browse_tool,
    column_outline: dict[str, Any],
    news: dict[str, Any],
) -> dict[str, Any] | None:
    logger.info("[Summarizing] %s", news["title"])
    content = await browse_tool.browse(news["url"])
    content = str(content or "").strip()
    if len(content) < config.settings.browse.min_content_length:
        logger.info("[Summarizing] Failed - content too short")
        return None
    if is_invalid_browse_content(content):
        logger.info("[Summarizing] Failed - invalid browsed content")
        return None

    summary_result = await (
        create_editor_agent(kind="column")
        .load_yaml_prompt(
            config.prompt_dir / "summarize_news.yaml",
            {
                "news_content": content,
                "news_title": news["title"],
                "column_requirement": column_outline["column_requirement"],
                "language": config.settings.workflow.output_language,
            },
        )
        .async_start(
            ensure_keys=[
                "can_summarize",
                "summary",
            ]
        )
    )

    if not isinstance(summary_result, dict):
        logger.info("[Summarizing] Failed - invalid summary output")
        return None
    if summary_result.get("can_summarize") is not True:
        logger.info("[Summarizing] Failed - model rejected content")
        return None

    summary = str(summary_result.get("summary") or "").strip()
    if not summary:
        logger.info("[Summarizing] Failed - empty summary")
        return None

    summarized_news = copy.deepcopy(news)
    summarized_news["summary"] = summary
    logger.info("[Summarizing] Success")
    return summarized_news


def build_backup_recommend_comment(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    news: dict[str, Any],
) -> str:
    title = str(column_outline.get("column_title") or "this section")
    news_title = str(news.get("title") or "").strip()
    if is_chinese_language(config.settings.workflow.output_language):
        if news_title:
            return f"该报道与“{title}”存在明确关联，可作为备用候选：{news_title}。"
        return f"该报道与“{title}”存在明确关联，可作为备用候选。"
    if news_title:
        return f"This story is meaningfully related to {title} and is kept as a backup candidate: {news_title}."
    return f"This story is meaningfully related to {title} and is kept as a backup candidate."


def is_invalid_browse_content(content: str) -> bool:
    normalized = content.strip()
    lowered = normalized.lower()
    invalid_markers = (
        "can not browse '",
        "fallback failed:",
        "content_empty_or_too_short",
        "we've detected unusual activity",
        "not a robot",
        "captcha",
        "access denied",
        "subscribe now",
    )
    return any(marker in lowered for marker in invalid_markers)


__all__ = [
    "create_prepare_summary_candidates_chunk",
    "create_dispatch_summary_batch_chunk",
    "create_summarize_candidate_chunk",
    "create_merge_summary_batch_chunk",
    "create_finalize_summary_chunk",
    "pick_news",
]
