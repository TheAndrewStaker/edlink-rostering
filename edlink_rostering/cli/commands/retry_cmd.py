"""``retry``: rewind the cursor of a failed sync_job and audit the retry.

Wraps :class:`edlink_rostering.services.retry.RetryService`. The CLI surface is
parallel to ``revert``: confirm-by-default, ``--yes`` to skip prompt,
``--operator`` to override the OS username. Adds ``--force`` to allow
retrying a successful sync (the service refuses by default).
"""

from __future__ import annotations

import asyncio
import getpass
import uuid

import click

from edlink_rostering.cli._db import session_factory
from edlink_rostering.services.retry import (
    RetryRefused,
    RetryService,
    RetrySyncJobNotFound,
)


@click.command("retry")
@click.argument("sync_job_id")
@click.option(
    "--reason",
    required=True,
    help="Free-text justification recorded in retry_actions.",
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
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Allow retry of a sync that ended in status='success'.",
)
def retry_cmd(
    sync_job_id: str,
    reason: str,
    operator: str | None,
    yes: bool,
    force: bool,
) -> None:
    """Rewind the cursor and audit a retry of a sync_job."""

    try:
        sjid = uuid.UUID(sync_job_id)
    except ValueError:
        raise click.BadParameter(
            f"{sync_job_id!r} is not a valid UUID."
        ) from None

    op_id = operator or getpass.getuser()
    if not yes:
        force_note = " (forced)" if force else ""
        click.confirm(
            f"Retry sync_job {sjid}{force_note} as {op_id!r} with reason {reason!r}?",
            abort=True,
        )

    asyncio.run(_run(sjid, op_id, reason, force))


async def _run(
    sync_job_id: uuid.UUID,
    operator: str,
    reason: str,
    forced: bool,
) -> None:
    service = RetryService(session_factory=session_factory())
    try:
        outcome = await service.retry(
            sync_job_id=sync_job_id,
            operator_identity=operator,
            reason=reason,
            forced=forced,
        )
    except RetrySyncJobNotFound as e:
        raise click.ClickException(str(e)) from None
    except RetryRefused as e:
        raise click.ClickException(str(e)) from None

    click.echo(
        f"Retry queued for sync_job {sync_job_id}. "
        f"lea_id={outcome.lea_id} partner={outcome.partner} "
        f"cursor_rewound_to={outcome.cursor_rewound_to or '(empty)'} "
        f"forced={outcome.forced}"
    )
    click.echo(
        "Next poll for this LEA will replay events from the rewound cursor."
    )
