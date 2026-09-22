from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ParserConfig:
    type: str
    pattern: str = ""
    group: str = ""
    expected: str = ""
    success_value: float = 1.0
    failure_value: float = 0.0
    value: float = 1.0
    state_label: str = "state"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ParserConfig:
        return cls(
            type=raw["type"],
            pattern=raw.get("pattern", ""),
            group=raw.get("group", ""),
            expected=raw.get("expected", ""),
            success_value=float(raw.get("success_value", raw.get("successValue", 1))),
            failure_value=float(raw.get("failure_value", raw.get("failureValue", 0))),
            value=float(raw.get("value", 1)),
            state_label=raw.get("state_label", raw.get("stateLabel", "state")),
        )


@dataclass
class MetricConfig:
    name: str
    description: str
    parser: ParserConfig
    labels: list[str] = field(
        default_factory=lambda: ["collector", "namespace", "pod", "protocol"]
    )

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> MetricConfig:
        return cls(
            name=raw["name"],
            description=raw.get("description", raw["name"]),
            labels=list(raw.get("labels", ["collector", "namespace", "pod", "protocol"])),
            parser=ParserConfig.from_raw(raw["parser"]),
        )


def _format_exec_command(exec_command: str, protocol: str, collector_name: str) -> str:
    if "{protocol}" not in exec_command:
        return exec_command
    if not protocol:
        raise ValueError(
            f"collector {collector_name}: protocol is required "
            "(set protocol in config or BIRD_PROTOCOL env)"
        )
    return exec_command.format(protocol=protocol)


@dataclass
class CollectorConfig:
    name: str
    namespace: str
    pod_name_regex: str
    exec_command: str
    exec_timeout_seconds: int = 30
    poll_interval_seconds: int | None = None
    protocol: str = ""
    collector_type: str = "generic_exec"
    metrics: list[MetricConfig] = field(default_factory=list)

    @property
    def protocol_label(self) -> str:
        return self.protocol or "unknown"

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> CollectorConfig:
        protocol = os.environ.get("BIRD_PROTOCOL") or raw.get("protocol", "")
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if f.name == "protocol":
                kwargs["protocol"] = protocol
            elif f.name == "exec_command":
                kwargs["exec_command"] = _format_exec_command(
                    raw["exec_command"],
                    protocol,
                    raw.get("name", "?"),
                )
            elif f.name == "exec_timeout_seconds":
                kwargs["exec_timeout_seconds"] = int(
                    raw.get("exec_timeout_seconds", raw.get("execTimeoutSeconds", 30))
                )
            elif f.name == "poll_interval_seconds":
                raw_interval = raw.get("poll_interval_seconds", raw.get("poll_interval_seconds"))
                kwargs["poll_interval_seconds"] = (
                    int(raw_interval) if raw_interval is not None else None
                )
            elif f.name == "metrics":
                kwargs["metrics"] = [
                    MetricConfig.from_raw(item) for item in raw.get("metrics", [])
                ]
            elif f.name in raw:
                kwargs[f.name] = raw[f.name]

        collector = cls(**kwargs)
        if not collector.metrics:
            raise ValueError(f"collector {collector.name}: at least one metric is required")
        return collector


@dataclass
class AppConfig:
    poll_interval_seconds: int = 60
    metrics_port: int = 8080
    collectors: list[CollectorConfig] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> AppConfig:
        collectors_raw = raw.get("collectors", [])
        if not collectors_raw:
            raise ValueError("At least one collector must be defined in config")

        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name == "collectors":
                kwargs["collectors"] = [
                    CollectorConfig.from_raw(item) for item in collectors_raw
                ]
            elif f.name in raw:
                kwargs[f.name] = int(raw[f.name])

        return cls(**kwargs)


def load_config(path: str | None = None) -> AppConfig:
    config_path_raw = path or os.environ.get("CONFIG_PATH") or "/config/config.yaml"
    # TODO
    if not config_path_raw:
        raise ValueError(f"Config set CONFIG_PATH")
    
    config_path = Path(config_path_raw)

    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: CONFIG_PATH")

    with config_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    return AppConfig.from_raw(raw)
