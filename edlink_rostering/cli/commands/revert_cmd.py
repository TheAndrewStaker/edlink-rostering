"""``revert``: soft-delete revert with operator confirmation.

Wraps :class:`edlink_rostering.services.revert.RevertService` and prints a
one-line summary that an operator can paste into a change ticket.
"""

from __future__ import annotations

import asyncio
import getpass
import uuid

import click

from edlink_rostering.cli._db import session_factory
from edlink_rostering.services.revert import (
    RevertRefused,
    RevertService,
    RevertSyncJobNotFound,
)


@click.command("revert")
@click.argument("sync_job_id")
@click.option(
    "--reason",
    required=True,
    help="Free-text justification recorded in revert_actions.",
)
@click.option(
    "--operator",
    default=None,
    help="Operator identity. Defaults to the OS username.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip the interactive confirmation. Use with care.",
)
def revert_cmd(
    sync_job_id: str,
    reason: str,
    operator: str | None,
    yes: bool,
) -> None:
    """Soft-delete revert of a sync_job."""

    try:
        sjid = uuid.UUID(sync_job_id)
    except ValueError:
        raise click.BadParameter(
            f"{sync_job_id!r} is not a valid UUID."
        ) from None

    op_id = operator or getpass.getuser()
    if not yes:
        click.confirm(
            f"Revert sync_job {sjid} as {op_id!r} with reason {reason!r}?",
            abort=True,
        )

    asyncio.run(_run(sjid, op_id, reason))


async def _run(sync_job_id: uuid.UUID, operator: str, reason: str) -> None:
    service = RevertService(session_factory=session_factory())
    try:
        outcome = await service.revert(
            sync_job_id=sync_job_id,
            operator_identity=operator,
            reason=reason,
        )
    except RevertSyncJobNotFound as e:
        raise click.ClickException(str(e)) from None
    except RevertRefused as e:
        raise click.ClickException(str(e)) from None

    click.echo(
        f"Reverted sync_job {sync_job_id}. "
        f"revert_generation_id={outcome.revert_generation_id} "
        f"snapshots_restored={outcome.snapshots_restored} "
        f"canonical_updated={outcome.canonical_rows_updated} "
        f"canonical_soft_deleted={outcome.canonical_rows_soft_deleted}"
    )
