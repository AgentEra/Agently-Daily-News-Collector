from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agently import TriggerFlowRuntimeData

from news_collector.markdown import render_markdown

from .common import DailyNewsChunkConfig, create_editor_agent, require_logger, safe_filename


def create_prepare_request_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def prepare_request(data: TriggerFlowRuntimeData) -> dict[str, Any]:
        topic = str(data.value).strip()
        now = datetime.now()
        request = {
            "topic": topic,
            "today": now.strftime("%Y-%m-%d"),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "language": config.settings.workflow.output_language,
        }
        data.state.set("request", request)
        require_logger(data).info("[Topic] %s", topic)
        return request

    return prepare_request


def create_generate_outline_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def generate_outline(data: TriggerFlowRuntimeData) -> list[dict[str, Any]]:
        request = data.value
        logger = require_logger(data)
        if config.settings.outline.use_customized:
            outline = _get_customized_outline(config)
            logger.info("[Use Customized Outline] %s", outline)
        else:
            outline = await _generate_outline(config, request)
            logger.info("[Outline Generated] %s", outline)
        data.state.set("outline", outline)
        return outline.get("column_list", [])

    return generate_outline


def create_render_report_chunk(
    config: DailyNewsChunkConfig,
) -> Callable[[TriggerFlowRuntimeData], Any]:
    async def render_report(data: TriggerFlowRuntimeData) -> dict[str, Any]:
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
        require_logger(data).info("[Markdown Saved] %s", output_path)
        return {
            "report_title": report_title,
            "output_path": str(output_path),
            "markdown": markdown,
            "columns": columns,
        }

    return render_report


async def _generate_outline(
    config: DailyNewsChunkConfig,
    request: dict[str, Any],
) -> dict[str, Any]:
    outline = await (
        create_editor_agent(kind="chief")
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


def _write_markdown(
    *,
    config: DailyNewsChunkConfig,
    report_title: str,
    report_date: str,
    markdown: str,
) -> Path:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{safe_filename(report_title)}_{report_date or datetime.now().strftime('%Y-%m-%d')}.md"
    output_path = config.output_dir / file_name
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


__all__ = [
    "create_prepare_request_chunk",
    "create_generate_outline_chunk",
    "create_render_report_chunk",
]
