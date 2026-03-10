from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(*, debug: bool, log_dir: str | Path) -> logging.Logger:
    target_dir = Path(log_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("agently_daily_news_collector")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(
        target_dir / "collector.log",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger
