from __future__ import annotations
import re
from prometheus_client import Counter, Gauge
from little_bird_exporter.config import CollectorConfig, MetricConfig, ParserConfig

collector_errors_total = Counter(
    "little_bird_exporter_collector_errors_total",
    "Total collector scrape errors",
    ["collector", "namespace", "pod"],
)

collector_pods_scraped = Gauge(
    "little_bird_exporter_pods_scraped",
    "Number of pods scraped in the last run",
    ["collector"],
)

last_scrape_timestamp = Gauge(
    "little_bird_exporter_last_scrape_timestamp_seconds",
    "Unix timestamp of the last successful scrape cycle",
)

scrape_duration_seconds = Gauge(
    "little_bird_exporter_scrape_duration_seconds",
    "Duration of the last scrape cycle in seconds",
)

scrape_success = Gauge(
    "s",
    "1 if the last scrape cycle completed, 0 otherwise",
)


def _safe_remove(metric: Gauge, *label_values: str) -> None:
    try:
        metric.remove(*label_values)
    except KeyError:
        pass

_DYNAMIC_GAUGES: dict[str, Gauge] = {}

def _get_or_create_gauge(metric_config: MetricConfig) -> Gauge:
    gauge = _DYNAMIC_GAUGES.get(metric_config.name)
    if gauge:
        return gauge

    gauge = Gauge(
        metric_config.name,
        metric_config.description,
        metric_config.labels,
    )
    _DYNAMIC_GAUGES[metric_config.name] = gauge
    return gauge


def _parse_metric_value(
    parser: ParserConfig,
    output: str,
) -> tuple[float, dict[str, str]]:
    if parser.type == "contains":
        value = parser.success_value if parser.pattern in output else parser.failure_value
        return value, {}

    if parser.type in {"regex_equals", "regex_value", "regex_state"}:
        match = re.search(parser.pattern, output, re.IGNORECASE)
        if not match:
            if parser.type == "regex_state":
                return parser.value, {parser.state_label: "unknown"}
            return parser.failure_value, {}

        raw_value = match.group(parser.group) if parser.group else match.group(1)

        if parser.type == "regex_equals":
            value = (
                parser.success_value
                if raw_value.lower() == parser.expected.lower()
                else parser.failure_value
            )
            return value, {}

        if parser.type == "regex_value":
            return float(raw_value), {}

        return parser.value, {parser.state_label: raw_value}

    if parser.type == "number":
        if not output.strip():
            return parser.failure_value, {}
        return float(output.strip()), {}

    raise ValueError(f"Unsupported parser types: {parser.type}")

class CollectorMetrics:
    def __init__(self, cfg: CollectorConfig) -> None:
        self.cfg = cfg
        self._gauges = {
            metric.name: _get_or_create_gauge(metric) for metric in cfg.metrics
        }
        self._last_label_values: dict[tuple[str, str], tuple[str, ...]] = {}
        self._label_history: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    def _labels(self, pod: str, **extra: str) -> dict[str, str]:
        return {
            "collector": self.cfg.name,
            "namespace": self.cfg.namespace,
            "pod": pod,
            "protocol": self.cfg.protocol_label,
            **extra,
        }

    def _base_values(self, pod: str) -> tuple[str, str, str, str]:
        labels = self._labels(pod)
        return (
            labels["collector"],
            labels["namespace"],
            labels["pod"],
            labels["protocol"],
        )

    def record_error(self, pod: str) -> None:
        collector_errors_total.labels(
            collector=self.cfg.name,
            namespace=self.cfg.namespace,
            pod=pod,
        ).inc()

    def set_pods_scraped(self, count: int) -> None:
        collector_pods_scraped.labels(collector=self.cfg.name).set(count)

    def update_metrics(self, pod: str, result: "ScrapeResult") -> None:
        for metric_config in self.cfg.metrics:
            value, extra_labels = _parse_metric_value(
                metric_config.parser,
                result.output,
            )
            labels = self._labels(pod, **extra_labels)
            label_values = tuple(labels[label] for label in metric_config.labels)
            cache_key = (metric_config.name, pod)
            old_label_values = self._last_label_values.get(cache_key)
            history = self._label_history.setdefault(cache_key, set())

            if old_label_values and old_label_values != label_values:
                if metric_config.parser.type == "regex_state":
                    self._gauges[metric_config.name].labels(*old_label_values).set(0)
                else:
                    _safe_remove(self._gauges[metric_config.name], *old_label_values)

            self._last_label_values[cache_key] = label_values
            history.add(label_values)
            if old_label_values:
                history.add(old_label_values)
            self._gauges[metric_config.name].labels(*label_values).set(value)

    def remove_pod(self, pod: str) -> None:
        for metric_config in self.cfg.metrics:
            cache_key = (metric_config.name, pod)
            label_values = self._last_label_values.pop(cache_key, None)
            label_values_to_remove = self._label_history.pop(cache_key, set())
            if label_values:
                label_values_to_remove.add(label_values)
            for known_label_values in label_values_to_remove:
                _safe_remove(self._gauges[metric_config.name], *known_label_values)

    def cleanup_stale(self, seen_pods: set[str]) -> None:
        known_pods = {pod for _, pod in self._last_label_values}
        for pod in known_pods - seen_pods:
            self.remove_pod(pod)
