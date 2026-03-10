from __future__ import annotations

import sys
from pathlib import Path

from .config import AppSettings
from .collector import DailyNewsCollector
from .logging_utils import configure_logging


ROOT_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT_DIR / "SETTINGS.yaml"


def main() -> int:
    settings = AppSettings.load(SETTINGS_PATH)
    logger = configure_logging(
        debug=settings.debug,
        log_dir=ROOT_DIR / "logs",
    )

    topic = " ".join(sys.argv[1:]).strip()
    if not topic:
        topic = input("请输入要生成新闻汇总的主题 / Please input the topic: ").strip()

    if not topic:
        print("Topic is required.")
        return 1

    collector = DailyNewsCollector(
        settings=settings,
        root_dir=ROOT_DIR,
        logger=logger,
    )

    try:
        result = collector.collect(topic)
    except Exception as exc:  # pragma: no cover - CLI guard
        logger.exception("Daily news collection failed: %s", exc)
        return 1

    print(result["markdown"])
    print(f"\n[Saved to] {result['output_path']}")
    return 0
