from __future__ import annotations

from pathlib import Path

from agently import TriggerFlow

from news_collector.config import AppSettings

from .chunks import (
    DailyNewsChunkConfig,
    create_generate_column_chunk,
    create_generate_outline_chunk,
    create_prepare_request_chunk,
    create_render_report_chunk,
)


def build_daily_news_flow(
    *,
    settings: AppSettings,
    root_dir: str | Path,
    model_label: str,
) -> TriggerFlow:
    resolved_root_dir = Path(root_dir).resolve()
    chunk_config = DailyNewsChunkConfig(
        settings=settings,
        prompt_dir=resolved_root_dir / "prompts",
        output_dir=resolved_root_dir / settings.output.directory,
        model_label=model_label,
    )
    flow = TriggerFlow(name="daily-news-collector-v4")
    prepare_request = flow.chunk("prepare_request")(create_prepare_request_chunk(chunk_config))
    generate_outline = flow.chunk("generate_outline")(create_generate_outline_chunk(chunk_config))
    generate_column = flow.chunk("generate_column")(create_generate_column_chunk(chunk_config))
    render_report = flow.chunk("render_report")(create_render_report_chunk(chunk_config))

    (
        flow.to(prepare_request)
        .to(generate_outline)
        .for_each(concurrency=settings.workflow.column_concurrency)
        .to(generate_column)
        .end_for_each()
        .to(render_report)
        .end()
    )

    return flow
