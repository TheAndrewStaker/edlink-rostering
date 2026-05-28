"""In-memory mock of Azure Application Insights / Azure Monitor exporters.

Mirrors the surface of the OpenTelemetry exporters the application would use in
production (``azure.monitor.opentelemetry``): a single ``Telemetry`` facade
with ``track_event``, ``track_metric``, and ``track_exception`` methods.

The mock writes to two sinks:

1. stdout, as structured JSON, one record per line. Tail-friendly during
   demos.
2. A rolling file at ``var/logs/app_insights.jsonl`` relative to the prototype
   directory. Keeps history across process restarts.

Tests can substitute a :class:`TelemetrySink` that captures records in memory
for assertions, without going through stdout or the filesystem.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from edlink_rostering.infrastructure.ports import (
    TelemetryRecord,
    TelemetrySink,
)


class StdoutSink:
    """Write structured JSON to stdout, one record per line."""

    def emit(self, record: TelemetryRecord) -> None:
        payload = {
            "kind": record.kind,
            "name": record.name,
            "occurred_at": record.occurred_at.isoformat(),
            "properties": record.properties,
            "measurements": record.measurements,
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()


class FileSink:
    """Append structured JSON to a file, one record per line."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: TelemetryRecord) -> None:
        payload = {
            "kind": record.kind,
            "name": record.name,
            "occurred_at": record.occurred_at.isoformat(),
            "properties": record.properties,
            "measurements": record.measurements,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")


class MemorySink:
    """Capture records in memory for tests to assert against."""

    def __init__(self) -> None:
        self.records: list[TelemetryRecord] = []

    def emit(self, record: TelemetryRecord) -> None:
        self.records.append(record)


class Telemetry:
    """Telemetry facade. Constructed once per process at startup.

    Default sinks: stdout + ``var/logs/app_insights.jsonl``. Tests pass a
    ``MemorySink`` as the only sink to capture emissions without I/O.
    """

    def __init__(self, sinks: list[TelemetrySink] | None = None) -> None:
        if sinks is None:
            sinks = [StdoutSink(), FileSink(Path("var/logs/app_insights.jsonl"))]
        self._sinks = sinks

    def track_event(
        self,
        name: str,
        properties: dict[str, str] | None = None,
        measurements: dict[str, float] | None = None,
    ) -> None:
        self._emit("event", name, properties, measurements)

    def track_metric(self, name: str, value: float) -> None:
        self._emit("metric", name, None, {name: value})

    def track_exception(
        self, exc: BaseException, properties: dict[str, str] | None = None
    ) -> None:
        props = dict(properties or {})
        props["exception_type"] = type(exc).__name__
        props["exception_message"] = str(exc)
        self._emit("exception", type(exc).__name__, props, None)

    def _emit(
        self,
        kind: str,
        name: str,
        properties: dict[str, str] | None,
        measurements: dict[str, float] | None,
    ) -> None:
        record = TelemetryRecord(
            kind=kind,
            name=name,
            occurred_at=datetime.now(UTC),
            properties=properties or {},
            measurements=measurements or {},
        )
        for sink in self._sinks:
            sink.emit(record)


def _ensure_protocol_satisfied() -> None:
    """Static-only check: every concrete sink satisfies TelemetrySink.

    Called at import time to surface signature drift in CI rather than at
    first emit.
    """

    sinks: list[TelemetrySink] = [
        StdoutSink(),
        MemorySink(),
    ]
    _ = sinks


_ensure_protocol_satisfied()
