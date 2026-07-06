from __future__ import annotations
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from kubernetes import client
from little_bird_exporter.config import CollectorConfig
from little_bird_exporter import kube
from little_bird_exporter.metrics import CollectorMetrics

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    pod: str
    output: str = ""
    error: str | None = None

    @classmethod
    def from_output(cls, pod: str, output: str) -> ScrapeResult:
        return cls(pod=pod, output=output)

    @classmethod
    def from_error(cls, pod: str, error: str) -> ScrapeResult:
        return cls(pod=pod, error=error)


class PodExecCollector:
    def __init__(self, cfg: CollectorConfig, core_v1: client.CoreV1Api) -> None:
        self.cfg = cfg
        self.core_v1 = core_v1
        self._pod_regex = re.compile(cfg.pod_name_regex)
        self.metrics = self.create_metrics()

    def create_metrics(self) -> CollectorMetrics:
        return CollectorMetrics(self.cfg)

    def _scrape_pod(self, pod_name: str) -> ScrapeResult:
        attempts = self.cfg.exec_retries + 1
        last_error = ""

        for attempt in range(attempts):
            try:
                output = kube.exec_in_pod(
                    self.core_v1,
                    self.cfg.namespace,
                    pod_name,
                    self.cfg.exec_command,
                    timeout=self.cfg.exec_timeout_seconds,
                )
                return ScrapeResult.from_output(pod_name, output)
            except Exception as exc:
                last_error = str(exc)
                if attempt < attempts - 1:
                    logger.warning(
                        "Retrying pod %s (attempt %s/%s): %s",
                        pod_name,
                        attempt + 2,
                        attempts,
                        exc,
                    )
                    continue

                logger.exception("Failed to scrape pod %s", pod_name)
                self.metrics.record_error(pod_name)
                return ScrapeResult.from_error(pod_name, last_error)

        return ScrapeResult.from_error(pod_name, last_error)

    def collect(self) -> list[ScrapeResult]:
        pod_names = kube.list_pods_matching(
            self.core_v1,
            self.cfg.namespace,
            self._pod_regex,
        )
        seen_pods = set(pod_names)
        results: list[ScrapeResult] = []

        if not pod_names:
            self.metrics.cleanup_stale(seen_pods)
            self.metrics.set_pods_scraped(0)
            return results

        max_workers = min(len(pod_names), 10)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._scrape_pod, pod_name): pod_name
                for pod_name in pod_names
            }
            for future in as_completed(futures):
                pod_name = futures[future]
                result = future.result()
                results.append(result)
                self.metrics.update_metrics(pod_name, result)

        self.metrics.cleanup_stale(seen_pods)
        self.metrics.set_pods_scraped(len(seen_pods))
        return results
