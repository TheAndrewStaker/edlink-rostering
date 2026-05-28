"""``list-syncs``: recent sync jobs for an LEA with status and counts."""

from __future__ import annotations

import asyncio
import sys

import click
from sqlalchemy import text

from edlink_rostering.cli._db import session_factory


@click.command("list-syncs")
@click.option("--lea", "lea_id", required=True, help="LEA identifier to filter on.")
@click.option("--limit", type=int, default=20, show_default=True)
@click.option(
    "--partner",
    default="edlink",
    show_default=True,
    help="Partner connector name. EdLink is the only shipped connector in session 1.",
)
def list_syncs(lea_id: str, limit: int, partner: str) -> None:
    """List recent sync_jobs for an LEA."""

    asyncio.run(_run(lea_id=lea_id, limit=limit, partner=partner))


async def _run(*, lea_id: str, limit: int, partner: str) -> None:
    factory = session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, status, started_at, completed_at,
                           event_count, error_count, warning_count,
                           cursor_before, cursor_after
                    FROM sync_jobs
                    WHERE lea_id = :lea_id AND partner = :partner
                    ORDER BY started_at DESC
                    LIMIT :limit
                    """
                ),
                {"lea_id": lea_id, "partner": partner, "limit": limit},
            )
        ).all()

    if not rows:
        click.echo(
            f"No sync_jobs for lea_id={lea_id} partner={partner}.",
            err=True,
        )
        sys.exit(0)

    header = (
        f"{'sync_job_id':>36}  {'status':<8}  {'events':>6}  "
        f"{'errors':>6}  {'warnings':>8}  {'cursor_after':<14}  started_at"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for r in rows:
        click.echo(
            f"{str(r.id):>36}  {r.status:<8}  {r.event_count:>6}  "
            f"{r.error_count:>6}  {r.warning_count:>8}  "
            f"{(r.cursor_after or ''):<14}  {r.started_at.isoformat()}"
        )
