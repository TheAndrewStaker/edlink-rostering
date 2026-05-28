"""Quarantine review CLI: list, release, reject.

Three commands sharing one module since they all operate on the same
table and one shared service object. The module-per-subject rule kicks
in once a family has its own commands; these are the family for
quarantine review.
"""

from __future__ import annotations

import asyncio
import getpass
import uuid

import click

from edlink_rostering.cli._db import session_factory
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.quarantine import (
    QuarantineAlreadyResolved,
    QuarantineNotFound,
    QuarantineRefused,
    QuarantineService,
)


@click.command("list-quarantine")
@click.option(
    "--lea",
    "lea_id",
    default=None,
    help="Filter to one LEA; omit for all LEAs.",
)
@click.option("--limit", type=int, default=50, show_default=True)
def list_quarantine(lea_id: str | None, limit: int) -> None:
    """List unresolved quarantine rows."""

    asyncio.run(_list(lea_id, limit))


async def _list(lea_id: str | None, limit: int) -> None:
    service = QuarantineService(session_factory=session_factory())
    rows = await service.list_unresolved(
        lea_id=LeaId(lea_id) if lea_id else None,
        limit=limit,
    )
    if not rows:
        click.echo("No unresolved quarantine rows.")
        return
    header = (
        f"{'quarantine_id':<36}  {'lea_id':<24}  {'entity_type':<10}  "
        f"{'entity_id':<14}  {'created_at':<26}  reason"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for r in rows:
        click.echo(
            f"{str(r.id):<36}  {r.lea_id:<24}  {r.entity_type:<10}  "
            f"{r.entity_id:<14}  {r.created_at.isoformat():<26}  "
            f"{r.reason}"
        )


@click.command("release-quarantine")
@click.argument("quarantine_id")
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
def release_quarantine(
    quarantine_id: str, operator: str | None, yes: bool
) -> None:
    """Re-validate and release a quarantined row to canonical."""

    try:
        qid = uuid.UUID(quarantine_id)
    except ValueError:
        raise click.BadParameter(
            f"{quarantine_id!r} is not a valid UUID."
        ) from None
    op_id = operator or getpass.getuser()
    if not yes:
        click.confirm(
            f"Release quarantine row {qid} as {op_id!r}?",
            abort=True,
        )

    asyncio.run(_release(qid, op_id))


async def _release(quarantine_id: uuid.UUID, operator: str) -> None:
    service = QuarantineService(session_factory=session_factory())
    try:
        outcome = await service.release(
            quarantine_id=quarantine_id,
            operator_identity=operator,
        )
    except QuarantineNotFound as e:
        raise click.ClickException(str(e)) from None
    except QuarantineAlreadyResolved as e:
        raise click.ClickException(str(e)) from None
    except QuarantineRefused as e:
        raise click.ClickException(str(e)) from None

    click.echo(
        f"Released {outcome.entity_type}/{outcome.entity_id}. "
        f"release_generation_id={outcome.release_generation_id}"
    )


@click.command("reject-quarantine")
@click.argument("quarantine_id")
@click.option(
    "--reason",
    required=True,
    help="Free-text justification recorded on the quarantine row.",
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
def reject_quarantine(
    quarantine_id: str,
    reason: str,
    operator: str | None,
    yes: bool,
) -> None:
    """Reject a quarantined row. No canonical change; audit only."""

    try:
        qid = uuid.UUID(quarantine_id)
    except ValueError:
        raise click.BadParameter(
            f"{quarantine_id!r} is not a valid UUID."
        ) from None
    op_id = operator or getpass.getuser()
    if not yes:
        click.confirm(
            f"Reject quarantine row {qid} as {op_id!r} with reason {reason!r}?",
            abort=True,
        )

    asyncio.run(_reject(qid, op_id, reason))


async def _reject(
    quarantine_id: uuid.UUID, operator: str, reason: str
) -> None:
    service = QuarantineService(session_factory=session_factory())
    try:
        outcome = await service.reject(
            quarantine_id=quarantine_id,
            operator_identity=operator,
            reason=reason,
        )
    except QuarantineNotFound as e:
        raise click.ClickException(str(e)) from None
    except QuarantineAlreadyResolved as e:
        raise click.ClickException(str(e)) from None

    click.echo(
        f"Rejected quarantine row {outcome.quarantine_id} at "
        f"{outcome.rejected_at.isoformat()}."
    )
