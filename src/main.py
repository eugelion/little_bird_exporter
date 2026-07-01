from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from kubernetes import client
from prometheus_client import start_http_server
from little_bird_exporter.collectors.base import PodExecCollector
from little_bird_exporter.collectors.generic_exec import GenericExecCollector
from little_bird_exporter.config import CollectorConfig, load_config
from little_bird_exporter.exporter import run_scrape_loop
from little_bird_exporter import kube

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

COLLECTOR_TYPES: dict[str, type[PodExecCollector]] = {
    "generic_exec": GenericExecCollector,
}

def build_collectors(
    configs: list[CollectorConfig],
    core_v1: client.CoreV1Api,
) -> list[PodExecCollector]:
    collectors: list[PodExecCollector] = []
    for cfg in configs:
        collector_cls = COLLECTOR_TYPES.get(cfg.collector_type, GenericExecCollector)
        collectors.append(collector_cls(cfg, core_v1))
    return collectors


def main() -> int:
    app_config = load_config()
    stop_event = threading.Event()

    def shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    core_v1 = kube.load_core_v1()
    collectors = build_collectors(app_config.collectors, core_v1)
    start_http_server(app_config.metrics_port)
    logger.info("Metrics server started on port %s", app_config.metrics_port)
    worker = threading.Thread(
        target=run_scrape_loop,
        args=(collectors, app_config.poll_interval_seconds, stop_event),
        name="scrape-loop",
        daemon=True,
    )
    worker.start()
    while not stop_event.is_set():
        time.sleep(1)

    worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
