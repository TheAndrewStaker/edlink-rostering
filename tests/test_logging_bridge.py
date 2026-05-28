"""Structlog stdlib bridge tests.

Pin the invariant the request-ID middleware promises: a stdlib log
record emitted from a non-migrated module (SQLAlchemy, uvicorn,
FastAPI, or any future caller of ``logging.getLogger``) carries the
``request_id`` bound by the middleware. Without the bridge, only
modules that use ``structlog.get_logger`` directly stitch into the
trace, which is the partial-migration footgun the Session 15 review
called out.

The bridge mechanism is :class:`structlog.stdlib.ProcessorFormatter`
with the structlog processor chain in ``foreign_pre_chain``; the test
proves it works end to end without depending on which framework
module emits the record.
"""

from __future__ import annotations

import io
import logging
import sys

import structlog

from edlink_rostering.core import logging as edlink_logging


def _force_reconfigure() -> None:
    """Tests have to re-run ``configure_logging`` to install a fresh handler.

    The module-level ``_CONFIGURED`` flag makes the real call
    idempotent in production; we toggle it here so the test installs
    a capture handler that lives only for the test.
    """

    edlink_logging._CONFIGURED = False
    edlink_logging.configure_logging(profile="")  # JSON renderer


def _capture_root_logs() -> io.StringIO:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    # Reuse the formatter installed by configure_logging so the captured
    # output matches what the production handler would emit.
    root = logging.getLogger()
    assert root.handlers, "configure_logging must install a handler first"
    handler.setFormatter(root.handlers[0].formatter)
    root.addHandler(handler)
    return buf


def test_stdlib_logger_picks_up_request_id_from_contextvar() -> None:
    """A ``logging.getLogger(...).info(...)`` call carries the request_id."""

    _force_reconfigure()
    buf = _capture_root_logs()

    # pytest's logging plugin can leave non-application loggers at a level
    # that filters INFO. Force the child logger's level explicitly so
    # the bridge invariant is what's under test, not pytest's defaults.
    target = logging.getLogger("sqlalchemy.engine")
    target.setLevel(logging.INFO)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="bridge-test-1234")
    try:
        # Use a stdlib logger from a fake "framework" namespace so this
        # mimics SQLAlchemy / uvicorn rather than an application module.
        target.info("BEGIN (implicit)")
    finally:
        structlog.contextvars.clear_contextvars()
        target.setLevel(logging.NOTSET)

    output = buf.getvalue()
    assert "bridge-test-1234" in output, output
    assert "BEGIN (implicit)" in output, output


def test_structlog_logger_picks_up_request_id_from_contextvar() -> None:
    """Symmetry: a ``structlog.get_logger`` call still gets request_id."""

    _force_reconfigure()
    buf = _capture_root_logs()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="bridge-test-5678")
    try:
        structlog.get_logger("edlink_rostering.test").info(
            "structlog_emit", extra_key="value"
        )
    finally:
        structlog.contextvars.clear_contextvars()

    output = buf.getvalue()
    assert "bridge-test-5678" in output, output
    assert "structlog_emit" in output, output


def test_stdlib_logger_without_request_id_still_emits() -> None:
    """No contextvar bound = no request_id key, but the record still ships."""

    _force_reconfigure()
    buf = _capture_root_logs()

    structlog.contextvars.clear_contextvars()
    logging.getLogger("alembic.runtime.migration").info("Running upgrade")

    output = buf.getvalue()
    assert "Running upgrade" in output, output
    # No request_id binding, so the rendered output must not falsely
    # carry one.
    assert "request_id" not in output, output
