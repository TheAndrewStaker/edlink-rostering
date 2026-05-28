"""HTTP admin API for the EdLink rostering framework.

A FastAPI surface over the same Postgres the sync worker and CLI write
into. Powers the Chakra UI admin app in ``web/`` and any
out-of-band scripts that want to talk to the integration framework over
HTTP rather than via the operator CLI.

Two responsibilities per endpoint family:

1. **Read endpoints** project sync_jobs, cursor_state, quarantine, and
   alerts feeds for the admin app. Tenant-scoped: every list endpoint
   takes ``lea_id`` (path or query) and the SQL queries always include
   it in the predicate.

2. **Action endpoints** wrap the same service classes the CLI uses
   (``RevertService``, ``RetryService``, ``QuarantineService``). The
   admin app drives them; the CLI keeps working unchanged.

Auth for the POC is a mock header (``X-Operator-Identity``). Iain's
walkthrough on day one will tell us which IdP to wire in; until then
the header is the seam.

The app object is exported from this module so callers (uvicorn,
tests) can import ``edlink_rostering.api:app``.

Windows event-loop note: uvicorn defaults to ``ProactorEventLoop`` on
Windows, which psycopg async refuses to use. The launcher at
``edlink_rostering/api/__main__.py`` switches to ``SelectorEventLoop`` via
``asyncio.run(..., loop_factory=...)`` (Python 3.14-recommended path).
For library callers (pytest, demo), each entry point sets up its own
event loop policy.
"""

from __future__ import annotations

from edlink_rostering.api.app import app

__all__ = ["app"]
