"""Mock of the Azure Functions invocation context.

Real Azure Functions runtime passes a ``Context`` object into each function
invocation carrying invocation_id, function name, function directory, and
bindings. The sync worker reads ``invocation_id`` to correlate its
``sync_jobs`` row with the runtime invocation, and reads bindings to discover
the Service Bus message that triggered it.

Mocked surface: just enough for the sync worker to construct its log fields
and operate on the bound message. The real Azure ``func.Context`` has more
fields; expand as needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FunctionContext:
    """One invocation's worth of metadata.

    function_name: the logical name of the function being invoked
        ("sync_worker", "poll_worker").
    invocation_id: a UUID that uniquely identifies this invocation. Surfaced
        on every log line and on the sync_jobs row.
    function_directory: the on-disk directory of the function (for production
        Azure Functions; in the mock, the prototype directory).
    bindings: arbitrary key-value pairs the runtime would inject (Service Bus
        message body, HTTP request, timer past-due flag).
    invocation_time: when the runtime began the invocation.
    """

    function_name: str
    invocation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    function_directory: Path = field(default_factory=lambda: Path.cwd())
    bindings: dict[str, Any] = field(default_factory=dict)
    invocation_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    def log_fields(self) -> dict[str, str]:
        """Standard structured-log fields for this invocation."""

        return {
            "function_name": self.function_name,
            "invocation_id": self.invocation_id,
            "invocation_time": self.invocation_time.isoformat(),
        }
