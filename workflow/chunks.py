from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, cast

from agently import Agently, TriggerFlowEventData

from news_collector.config import AppSettings
from news_collector.markdown import render_markdown
from tools import BrowseToolProtocol, SearchToolProtocol


@dataclass(frozen=True, slots=True)
class DailyNewsChunkConfig:
    settings: AppSettings
    prompt_dir: Path
    output_dir: Path
    model_label: str


def create_prepare_request_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowEventData], Any]:
    async def prepare_request(data: TriggerFlowEventData) -> dict[str, Any]:
        topic = str(data.value).strip()
        now = datetime.now()
        request = {
            "topic": topic,
            "today": now.strftime("%Y-%m-%d"),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "language": config.settings.workflow.output_language,
        }
        data.state.set("request", request)
        _require_logger(data).info("[Topic] %s", topic)
        return request

    return prepare_request


def create_generate_outline_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowEventData], Any]:
    async def generate_outline(data: TriggerFlowEventData) -> list[dict[str, Any]]:
        request = data.value
        logger = _require_logger(data)
        if config.settings.outline.use_customized:
            outline = _get_customized_outline(config)
            logger.info("[Use Customized Outline] %s", outline)
        else:
            outline = await _generate_outline(config, request)
            logger.info("[Outline Generated] %s", outline)
        data.state.set("outline", outline)
        return outline.get("column_list", [])

    return generate_outline


def create_generate_column_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowEventData], Any]:
    async def generate_column(data: TriggerFlowEventData) -> dict[str, Any] | None:
        return await _generate_column(config, data, data.value)

    return generate_column


def create_render_report_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowEventData], Any]:
    async def render_report(data: TriggerFlowEventData) -> dict[str, Any]:
        request = data.state.get("request") or {}
        outline = data.state.get("outline") or {}
        columns = [column for column in data.value if isinstance(column, dict)]
        report_title = str(
            outline.get("report_title")
            or f"Daily News about {request.get('topic', 'the topic')}"
        )
        markdown = render_markdown(
            report_title=report_title,
            generated_at=str(request.get("generated_at") or ""),
            topic=str(request.get("topic") or ""),
            language=config.settings.workflow.output_language,
            columns=columns,
            model_label=config.model_label,
        )
        output_path = _write_markdown(
            config=config,
            report_title=report_title,
            report_date=str(request.get("today") or ""),
            markdown=markdown,
        )
        _require_logger(data).info("[Markdown Saved] %s", output_path)
        return {
            "report_title": report_title,
            "output_path": str(output_path),
            "markdown": markdown,
            "columns": columns,
        }

    return render_report


def _create_agent(*, kind: str):
    agent = Agently.create_agent(name=f"{kind}_editor")
    if kind == "chief":
        agent.set_agent_prompt(
            "system",
            "You are a veteran newsroom chief editor who designs reliable daily news briefings.",
        )
        agent.set_agent_prompt(
            "instruct",
            [
                "Prefer recent, factual, non-duplicated stories.",
                "Keep structures stable and concise.",
            ],
        )
    else:
        agent.set_agent_prompt(
            "system",
            "You are a meticulous news editor who selects and rewrites high-signal stories.",
        )
        agent.set_agent_prompt(
            "instruct",
            [
                "Reject irrelevant or thin content.",
                "Keep comments practical and publication-ready.",
            ],
        )
    return agent


async def _generate_outline(
    config: DailyNewsChunkConfig,
    request: dict[str, Any],
) -> dict[str, Any]:
    outline = await (
        _create_agent(kind="chief")
        .load_yaml_prompt(
            config.prompt_dir / "create_outline.yaml",
            {
                "topic": request["topic"],
                "today": request["today"],
                "language": config.settings.workflow.output_language,
                "max_column_num": config.settings.workflow.max_column_num,
            },
        )
        .async_start(
            ensure_keys=[
                "report_title",
                "column_list[*].column_title",
                "column_list[*].column_requirement",
                "column_list[*].search_keywords",
            ]
        )
    )
    if not isinstance(outline, dict):
        raise TypeError(f"Invalid outline result: {outline}")
    column_list = outline.get("column_list", [])
    if not isinstance(column_list, list):
        raise TypeError("Outline column_list must be a list.")
    outline["column_list"] = column_list[: config.settings.workflow.max_column_num]
    return outline


def _get_customized_outline(config: DailyNewsChunkConfig) -> dict[str, Any]:
    outline = copy.deepcopy(config.settings.outline.customized)
    column_list = outline.get("column_list", [])
    if not isinstance(column_list, list) or not column_list:
        raise ValueError("Customized outline must provide a non-empty column_list.")
    outline["column_list"] = column_list[: config.settings.workflow.max_column_num]
    outline.setdefault("report_title", "Daily News Briefing")
    return outline


async def _generate_column(
    config: DailyNewsChunkConfig,
    data: TriggerFlowEventData,
    column_outline: dict[str, Any],
) -> dict[str, Any] | None:
    title = str(column_outline.get("column_title") or "").strip()
    if not title:
        return None

    logger = _require_logger(data)
    logger.info("[Start Generate Column] %s", title)
    try:
        request = data.state.get("request") or {}
        search_tool = _require_search_tool(data)
        browse_tool = _require_browse_tool(data)
        searched_news = await _search_news(
            config,
            logger,
            search_tool,
            column_outline,
            topic=str(request.get("topic") or ""),
        )
        logger.info("[Search News Count] %s => %s", title, len(searched_news))
        if not searched_news:
            return None

        picked_news = await _pick_news(config, column_outline, searched_news)
        logger.info("[Picked News Count] %s => %s", title, len(picked_news))
        if not picked_news:
            return None

        summarized_news = await _summarize_news(
            config,
            logger,
            browse_tool,
            column_outline,
            searched_news,
            picked_news,
        )
        logger.info("[Summarized News Count] %s => %s", title, len(summarized_news))
        if not summarized_news:
            return None

        column_result = await _write_column(config, column_outline, summarized_news)
        logger.info("[Column Ready] %s", title)
        return column_result
    except Exception as exc:
        logger.exception("[Column Failed] %s: %s", title, exc)
        return None


async def _search_news(
    config: DailyNewsChunkConfig,
    logger: logging.Logger,
    search_tool: SearchToolProtocol,
    column_outline: dict[str, Any],
    *,
    topic: str,
) -> list[dict[str, Any]]:
    query = str(column_outline.get("search_keywords") or "").strip()
    if not query:
        return []
    queries = _build_search_queries(
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


def _build_search_queries(
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


async def _pick_news(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    searched_news: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pick_results = await (
        _create_agent(kind="column")
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
        key=lambda item: _safe_int(item.get("relevance_score"), 0),
        reverse=True,
    )
    for item in sorted_results:
        if item.get("can_use") is not True:
            continue
        news_id = _safe_int(item.get("id"), -1)
        if news_id < 0 or news_id >= len(searched_news) or news_id in seen_ids:
            continue
        seen_ids.add(news_id)
        picked_item = copy.deepcopy(searched_news[news_id])
        picked_item["recommend_comment"] = str(item.get("recommend_comment") or "").strip()
        picked_item["relevance_score"] = _safe_int(item.get("relevance_score"), 0)
        picked_news.append(picked_item)
        if len(picked_news) >= config.settings.workflow.max_news_per_column:
            break
    return picked_news


async def _summarize_news(
    config: DailyNewsChunkConfig,
    logger: logging.Logger,
    browse_tool: BrowseToolProtocol,
    column_outline: dict[str, Any],
    searched_news: list[dict[str, Any]],
    picked_news: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(config.settings.workflow.summary_concurrency)

    async def worker(news: dict[str, Any]) -> dict[str, Any] | None:
        async with semaphore:
            return await _summarize_single_news(
                config,
                logger,
                browse_tool,
                column_outline,
                news,
            )

    target_count = min(
        len(picked_news),
        config.settings.workflow.max_news_per_column,
    )
    candidates = _build_summary_candidates(
        config,
        column_outline,
        searched_news,
        picked_news,
    )
    summarized_news = []
    cursor = 0

    while len(summarized_news) < target_count and cursor < len(candidates):
        needed_count = target_count - len(summarized_news)
        batch_size = min(
            max(config.settings.workflow.summary_concurrency, 1),
            needed_count,
            len(candidates) - cursor,
        )
        batch = candidates[cursor : cursor + batch_size]
        cursor += batch_size

        tasks = [worker(news) for news, _ in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (news, is_backup), result in zip(batch, results):
            if isinstance(result, Exception):
                logger.error("[Summarizing] Unexpected error: %s", result)
                continue
            if result:
                summarized_news.append(result)
                continue
            if is_backup:
                logger.info("[Backup News Rejected] %s", news["title"])
            elif cursor < len(candidates):
                logger.info("[Backup News Activated] %s", news["title"])
    return summarized_news


async def _summarize_single_news(
    config: DailyNewsChunkConfig,
    logger: logging.Logger,
    browse_tool: BrowseToolProtocol,
    column_outline: dict[str, Any],
    news: dict[str, Any],
) -> dict[str, Any] | None:
    logger.info("[Summarizing] %s", news["title"])
    content = await browse_tool.browse(news["url"])
    content = str(content or "").strip()
    if len(content) < config.settings.browse.min_content_length:
        logger.info("[Summarizing] Failed - content too short")
        return None
    if _is_invalid_browse_content(content):
        logger.info("[Summarizing] Failed - invalid browsed content")
        return None

    summary_result = await (
        _create_agent(kind="column")
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


def _build_summary_candidates(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    searched_news: list[dict[str, Any]],
    picked_news: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], bool]]:
    candidates: list[tuple[dict[str, Any], bool]] = []
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
        candidates.append((copy.deepcopy(news), False))

    for news in searched_news:
        url = str(news.get("url") or "").strip()
        if not url or url in seen_urls or url in picked_urls:
            continue
        seen_urls.add(url)
        backup_news = copy.deepcopy(news)
        if not str(backup_news.get("recommend_comment") or "").strip():
            backup_news["recommend_comment"] = _build_backup_recommend_comment(
                config,
                column_outline,
                backup_news,
            )
        candidates.append((backup_news, True))

    return candidates


def _build_backup_recommend_comment(
    config: DailyNewsChunkConfig,
    column_outline: dict[str, Any],
    news: dict[str, Any],
) -> str:
    title = str(column_outline.get("column_title") or "this section")
    news_title = str(news.get("title") or "").strip()
    if _is_chinese_language(config.settings.workflow.output_language):
        if news_title:
            return f"该报道与“{title}”存在明确关联，可作为备用候选：{news_title}。"
        return f"该报道与“{title}”存在明确关联，可作为备用候选。"
    if news_title:
        return f"This story is meaningfully related to {title} and is kept as a backup candidate: {news_title}."
    return f"This story is meaningfully related to {title} and is kept as a backup candidate."


def _is_invalid_browse_content(content: str) -> bool:
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
        _create_agent(kind="column")
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
        news_id = _safe_int(item.get("id"), -1)
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

    if _is_chinese_language(config.settings.workflow.output_language):
        lead_titles = "，".join(f"《{news['title']}》" for news in news_list[:3])
        return f"本栏目围绕“{column_outline['column_title']}”整理了以下重点内容：{lead_titles}。"

    lead_titles = ", ".join(news["title"] for news in news_list[:3])
    return f"This section highlights the most relevant stories for {column_outline['column_title']}: {lead_titles}."


def _write_markdown(
    *,
    config: DailyNewsChunkConfig,
    report_title: str,
    report_date: str,
    markdown: str,
) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = _safe_filename(report_title)
    file_name = f"{safe_title}_{report_date or datetime.now().strftime('%Y-%m-%d')}.md"
    output_path = config.output_dir / file_name
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def _is_chinese_language(language: str) -> bool:
    normalized = language.lower()
    return "chinese" in normalized or normalized.startswith("zh")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    return cleaned or "daily-news-report"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _require_logger(data: TriggerFlowEventData) -> logging.Logger:
    return cast(logging.Logger, data.require_resource("logger"))


def _require_search_tool(data: TriggerFlowEventData) -> SearchToolProtocol:
    return cast(SearchToolProtocol, data.require_resource("search_tool"))


def _require_browse_tool(data: TriggerFlowEventData) -> BrowseToolProtocol:
    return cast(BrowseToolProtocol, data.require_resource("browse_tool"))
