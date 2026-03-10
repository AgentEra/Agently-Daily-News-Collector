from __future__ import annotations

from pathlib import Path

from agently import TriggerFlow

from news_collector.config import AppSettings

from .column_chunks import (
    create_pick_column_news_chunk,
    create_search_column_news_chunk,
    create_write_column_chunk,
)
from .common import DailyNewsChunkConfig
from .report_chunks import (
    create_generate_outline_chunk,
    create_prepare_request_chunk,
    create_render_report_chunk,
)
from .summary_chunks import (
    create_dispatch_summary_batch_chunk,
    create_finalize_summary_chunk,
    create_merge_summary_batch_chunk,
    create_prepare_summary_candidates_chunk,
    create_summarize_candidate_chunk,
)


def build_summary_sub_flow(
    *,
    chunk_config: DailyNewsChunkConfig,
) -> TriggerFlow:
    flow = TriggerFlow(name="daily-news-summary-sub-flow")
    prepare_summary_candidates = flow.chunk("prepare_summary_candidates")(
        create_prepare_summary_candidates_chunk(chunk_config)
    )
    dispatch_summary_batch = flow.chunk("dispatch_summary_batch")(
        create_dispatch_summary_batch_chunk(chunk_config)
    )
    summarize_candidate = flow.chunk("summarize_candidate")(
        create_summarize_candidate_chunk(chunk_config)
    )
    merge_summary_batch = flow.chunk("merge_summary_batch")(
        create_merge_summary_batch_chunk(chunk_config)
    )
    finalize_summary = flow.chunk("finalize_summary")(
        create_finalize_summary_chunk(chunk_config)
    )

    (
        flow.when("Summary.Dispatch")
        .to(dispatch_summary_batch)
        .for_each(concurrency=chunk_config.settings.workflow.summary_concurrency)
        .to(summarize_candidate)
        .end_for_each()
        .to(merge_summary_batch)
    )
    flow.when("Summary.Done").to(finalize_summary).end()
    flow.to(prepare_summary_candidates)
    return flow


def build_column_sub_flow(
    *,
    chunk_config: DailyNewsChunkConfig,
) -> TriggerFlow:
    flow = TriggerFlow(name="daily-news-column-sub-flow")
    summary_sub_flow = build_summary_sub_flow(chunk_config=chunk_config)
    search_column_news = flow.chunk("search_column_news")(create_search_column_news_chunk(chunk_config))
    pick_column_news = flow.chunk("pick_column_news")(create_pick_column_news_chunk(chunk_config))
    write_column = flow.chunk("write_column")(create_write_column_chunk(chunk_config))

    (
        flow.to(search_column_news)
        .to(pick_column_news)
        .to_sub_flow(
            summary_sub_flow,
            capture={
                "input": "value",
                "resources": {
                    "logger": "resources.logger",
                    "browse_tool": "resources.browse_tool",
                },
            },
            write_back={
                "value": "result",
            },
        )
        .to(write_column)
        .end()
    )
    return flow


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
    column_sub_flow = build_column_sub_flow(chunk_config=chunk_config)
    prepare_request = flow.chunk("prepare_request")(create_prepare_request_chunk(chunk_config))
    generate_outline = flow.chunk("generate_outline")(create_generate_outline_chunk(chunk_config))
    render_report = flow.chunk("render_report")(create_render_report_chunk(chunk_config))

    (
        flow.to(prepare_request)
        .to(generate_outline)
        .for_each(concurrency=settings.workflow.column_concurrency)
        .to_sub_flow(
            column_sub_flow,
            capture={
                "input": "value",
                "runtime_data": {
                    "request": "runtime_data.request",
                },
                "resources": {
                    "logger": "resources.logger",
                    "search_tool": "resources.search_tool",
                    "browse_tool": "resources.browse_tool",
                },
            },
            write_back={
                "value": "result",
            },
        )
        .end_for_each()
        .to(render_report)
        .end()
    )

    return flow
