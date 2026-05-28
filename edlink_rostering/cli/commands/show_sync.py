"""``show-sync``: detail view for one sync_job.

Surfaces what the operator needs to triage:

- The sync_jobs row (status, counts, cursors, timing).
- Per-layer validation issue codes.
- Any quarantine rows for this sync.
- Any revert_actions targeting this sync.
"""

from __future__ import annotations

import asyncio
import uuid

import click
from sqlalchemy import text

from edlink_rostering.cli._db import session_factory


@click.command("show-sync")
@click.argument("sync_job_id")
def show_sync(sync_job_id: str) -> None:
    """Detail view for one sync_job."""

    try:
        sjid = uuid.UUID(sync_job_id)
    except ValueError:
        raise click.BadParameter(
            f"{sync_job_id!r} is not a valid UUID."
        ) from None
    asyncio.run(_run(sjid))


async def _run(sync_job_id: uuid.UUID) -> None:
    factory = session_factory()
    async with factory() as session:
        sync_row = (
            await session.execute(
                text(
                    """
                    SELECT id, lea_id, partner, status, started_at,
                           completed_at, event_count, error_count,
                           warning_count, cursor_before, cursor_after,
                           error_summary
                    FROM sync_jobs WHERE id = :id
                    """
                ),
                {"id": sync_job_id},
            )
        ).first()
        if sync_row is None:
            raise click.ClickException(
                f"sync_job_id {sync_job_id} not found."
            )

        validations = (
            await session.execute(
                text(
                    """
                    SELECT layer, code, payload_reference, created_at
                    FROM sync_validation_results
                    WHERE sync_job_id = :id
                    ORDER BY layer, created_at
                    """
                ),
                {"id": sync_job_id},
            )
        ).all()

        quarantine_rows = (
            await session.execute(
                text(
                    """
                    SELECT entity_type, entity_id, reason, created_at
                    FROM quarantine WHERE sync_job_id = :id
                    """
                ),
                {"id": sync_job_id},
            )
        ).all()

        revert_rows = (
            await session.execute(
                text(
                    """
                    SELECT id, revert_generation_id, operator_identity,
                           reason, reverted_at, snapshots_restored
                    FROM revert_actions WHERE sync_job_id = :id
                    ORDER BY reverted_at
                    """
                ),
                {"id": sync_job_id},
            )
        ).all()

    click.echo(f"sync_job_id      : {sync_row.id}")
    click.echo(f"lea_id           : {sync_row.lea_id}")
    click.echo(f"partner          : {sync_row.partner}")
    click.echo(f"status           : {sync_row.status}")
    click.echo(f"started_at       : {sync_row.started_at.isoformat()}")
    click.echo(
        "completed_at     : "
        f"{sync_row.completed_at.isoformat() if sync_row.completed_at else '(in progress)'}"
    )
    click.echo(f"event_count      : {sync_row.event_count}")
    click.echo(f"error_count      : {sync_row.error_count}")
    click.echo(f"warning_count    : {sync_row.warning_count}")
    click.echo(f"cursor_before    : {sync_row.cursor_before or '(empty)'}")
    click.echo(f"cursor_after     : {sync_row.cursor_after or '(empty)'}")
    if sync_row.error_summary:
        click.echo(f"error_summary    : {sync_row.error_summary}")

    if validations:
        click.echo("")
        click.echo("Validation results:")
        for v in validations:
            ref = f" @{v.payload_reference}" if v.payload_reference else ""
            click.echo(f"  Layer {v.layer}: {v.code}{ref}")

    if quarantine_rows:
        click.echo("")
        click.echo("Quarantined rows:")
        for q in quarantine_rows:
            click.echo(
                f"  {q.entity_type}/{q.entity_id}: {q.reason}"
            )

    if revert_rows:
        click.echo("")
        click.echo("Revert history:")
        for r in revert_rows:
            click.echo(
                f"  {r.reverted_at.isoformat()} by {r.operator_identity}: "
                f"{r.snapshots_restored} snapshot(s) restored. "
                f"Reason: {r.reason}"
            )
