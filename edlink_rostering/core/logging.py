"""structlog configuration for the application runtime.

Configures structlog once so every module that calls
``structlog.get_logger(__name__)`` produces the same output shape AND
every framework module that calls ``logging.getLogger(__name__)``
(SQLAlchemy, uvicorn, FastAPI, alembic) lands its records through the
same renderer with the same request-scoped context bound.

Two output modes:

* **dev profile** (``EDLINK_PROFILE=dev``): ConsoleRenderer, no
  colors (Windows terminals do not always honor ANSI), one line per
  event with key=value pairs after the event name.
* **anything else (prod default)**: JSONRenderer, one JSON object per
  line, suitable for log aggregators (Azure Application Insights,
  Datadog, etc.).

Request-scoped context (request_id, traceparent, lea_id, sync_job_id)
is bound via :mod:`structlog.contextvars` from the request-ID
middleware in :mod:`edlink_rostering.api.middleware`. The
``merge_contextvars`` processor sits in BOTH the structlog pipeline
and the stdlib bridge's ``foreign_pre_chain`` so a stdlib log record
emitted from inside a request also carries ``request_id``. Without the
bridge, only the three modules that already use ``structlog.get_logger``
would stitch into the trace; SQLAlchemy SQL traces, uvicorn access
lines, and any future module that drops a stdlib log line would be
orphaned. With the bridge, the trace is whole.

The bridge piece is :class:`structlog.stdlib.ProcessorFormatter`. The
formatter receives a stdlib ``LogRecord``, runs the
``foreign_pre_chain`` (which is structlog's processor list), then
hands the event dict to the final ``processor=renderer``. Structlog
loggers route through the same formatter via
``ProcessorFormatter.wrap_for_formatter`` so both producer paths emit
identical line shapes.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor


_CONFIGURED = False


def configure_logging(*, profile: str = "") -> None:
    """Idempotent one-shot configuration.

    Safe to call from both ``api/app.py`` module-import and from a CLI
    entry point; the second call is a no-op so test harnesses that
    invoke both paths do not double-register processors.
    """

    global _CONFIGURED
    if _CONFIGURED:
        return

    # The pre-chain runs on every record (structlog and stdlib alike)
    # before the final renderer. ``merge_contextvars`` is what pulls
    # ``request_id`` from the contextvar bound by the request-ID
    # middleware into the event dict, so a stdlib SQLAlchemy log line
    # emitted during a request carries the same request_id as the
    # surrounding structlog calls.
    pre_chain: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: Processor
    if profile == "dev":
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    # Structlog loggers terminate their processor chain with
    # ``wrap_for_formatter`` so the final rendering happens once, in
    # the stdlib handler's formatter, instead of producing a fully
    # rendered string here. This is what makes the stdlib bridge
    # symmetric.
    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processor=renderer,
    )

    # Replace any handlers a prior ``logging.basicConfig`` call might
    # have installed (e.g. uvicorn's default StreamHandler). Tests
    # toggle ``_CONFIGURED`` to re-run this, so the clear is necessary
    # or the same record gets emitted twice through different
    # formatters.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    _CONFIGURED = True


__all__ = ["configure_logging"]
