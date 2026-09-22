from __future__ import annotations

import logging
import threading
import time

from little_bird_exporter.collectors.base import PodExecCollector, ScrapeResult
from little_bird_exporter.metrics import (
    last_scrape_timestamp,
    scrape_duration_seconds,
    scrape_success,
)

logger = logging.getLogger(__name__)


def _log_result(collector: PodExecCollector, result: ScrapeResult) -> None:
    if result.error:
        logger.warning(
            "collector=%s pod=%s error=%s",
            collector.cfg.name,
            result.pod,
            result.error,
        )
        return

    logger.info(
        "collector=%s pod=%s output_bytes=%s",
        collector.cfg.name,
        result.pod,
        len(result.output),
    )


def run_scrape_loop(
    collectors: list[PodExecCollector],
    interval_seconds: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        started = time.time()
        try:
            for collector in collectors:
                for result in collector.collect():
                    _log_result(collector, result)
            scrape_success.set(1)
            last_scrape_timestamp.set(time.time())
        except Exception:
            scrape_success.set(0)
            logger.exception("Scrape cycle failed")

        scrape_duration_seconds.set(time.time() - started)

        stop_event.wait(interval_seconds)
