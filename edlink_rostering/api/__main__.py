"""Launcher for the FastAPI admin server.

uvicorn owns its own event loop and creates it before importing the
target app, so any in-app policy switch lands too late on Windows.
psycopg async refuses to run on ``ProactorEventLoop`` (uvicorn's
Windows default), so the launcher has two paths:

1. **No-reload (the default and what the scripts use).** Build
   uvicorn's ``Server`` programmatically and drive it with
   ``asyncio.run(..., loop_factory=SelectorEventLoop)``. This is the
   Python 3.12+ replacement for the deprecated
   ``set_event_loop_policy(WindowsSelectorEventLoopPolicy())`` pair.

2. **Reload.** Uvicorn's reload mode spawns a watchdog parent and a
   child subprocess via ``multiprocessing.spawn``. To make the
   selector-loop fix apply to the child, we set
   ``WindowsSelectorEventLoopPolicy`` as the policy BEFORE
   ``uvicorn.run`` so each spawned child inherits it. The
   deprecation warning is filtered in ``pyproject.toml`` because this
   is the only mechanism uvicorn reload + Windows + psycopg combine
   on. Reload also has a bad failure mode: when the parent process
   dies, the child can outlive it and continue serving stale code on
   the bound port. The default scripts run without ``--reload`` to
   avoid that.

``scripts/api-serve.sh`` invokes ``python -m edlink_rostering.api`` so this is
the canonical dev entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the admin API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    if sys.platform == "win32" and not args.reload:
        config = uvicorn.Config(
            "edlink_rostering.api:app",
            host=args.host,
            port=args.port,
            loop="none",
            lifespan="on",
            log_level="info",
        )
        server = uvicorn.Server(config)
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
        return

    if sys.platform == "win32" and args.reload:
        # Reload child inherits the policy set on the parent before
        # uvicorn.run runs.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run(
        "edlink_rostering.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
