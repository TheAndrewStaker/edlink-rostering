"""``cursor-status``: cursor position and lag per LEA.

Surfaces the 20-day cursor-lag signal that drives the day-one alert in
``edlink-oneroster-rostering.md``. The CLI computes
``days_behind = now - last_event_at`` and flags anything past 20 days.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import click
from sqlalchemy import text

from edlink_rostering.cli._db import session_factory


_LAG_ALERT_DAYS = 20


@click.command("cursor-status")
@click.option(
    "--lea",
    "lea_id",
    default=None,
    help="Filter to one LEA; omit for all LEAs.",
)
def cursor_status(lea_id: str | None) -> None:
    """Show cursor position and lag for each LEA."""

    asyncio.run(_run(lea_id))


async def _run(lea_id: str | None) -> None:
    factory = session_factory()
    async with factory() as session:
        if lea_id is None:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT lea_id, partner, last_event_id, last_event_at,
                               last_poll_at, cold_start_required
                        FROM cursor_state
                        ORDER BY lea_id, partner
                        """
                    ),
                )
            ).all()
        else:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT lea_id, partner, last_event_id, last_event_at,
                               last_poll_at, cold_start_required
                        FROM cursor_state
                        WHERE lea_id = :lea_id
                        ORDER BY partner
                        """
                    ),
                    {"lea_id": lea_id},
                )
            ).all()

    if not rows:
        click.echo("No cursors recorded.", err=True)
        return

    now = datetime.now(UTC)
    header = (
        f"{'lea_id':<28}  {'partner':<10}  {'last_event_id':<14}  "
        f"{'last_event_at':<26}  {'days_behind':>11}  cold_start"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for r in rows:
        last_at = r.last_event_at
        days_behind = (
            (now - last_at).total_seconds() / 86400.0
            if last_at is not None
            else float("inf")
        )
        flag = "ALERT" if days_behind > _LAG_ALERT_DAYS else "ok"
        click.echo(
            f"{r.lea_id:<28}  {r.partner:<10}  "
            f"{(r.last_event_id or '-'):<14}  "
            f"{(last_at.isoformat() if last_at else '(none)'):<26}  "
            f"{days_behind:>10.2f}d  "
            f"{flag}{'  cold_start' if r.cold_start_required else ''}"
        )
