"""``reconcile``: force a single-pair reconciliation outside the daily sweep.

Wraps :class:`edlink_rostering.services.reconciliation_scheduler.ReconciliationScheduler`
with ``force=True`` so the operator can investigate drift mid-day
without waiting for the 02:00 LEA-local sweep. Prints status, root
hashes, and the per-entity-type drift summary if one is present.

The partner-side snapshot is pulled from the EdLink connector's
``walk_resources``. Production registers more partners (Ednition,
Clever) in the dispatch table at :mod:`edlink_rostering.cli._snapshot`; the
POC ships with EdLink only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from edlink_rostering.cli._db import session_factory
from edlink_rostering.cli._snapshot import (
    default_fixtures_dir,
    make_snapshot_provider,
)
from edlink_rostering.core.types import LeaId
from edlink_rostering.services.reconciliation import (
    ReconciliationReport,
    ReconciliationService,
)
from edlink_rostering.services.reconciliation_scheduler import (
    ReconciliationScheduler,
)


@click.command("reconcile")
@click.argument("lea_id")
@click.option(
    "--partner",
    default="edlink",
    show_default=True,
    help="Partner connector name. Only 'edlink' is wired in the POC.",
)
@click.option(
    "--force/--no-force",
    default=True,
    show_default=True,
    help="Bypass the 60-minute quiet-window check (operator investigation).",
)
@click.option(
    "--fixtures-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "EdLink fixtures directory. Defaults to fixtures/edlink"
        " relative to the CLI module."
    ),
)
def reconcile_cmd(
    lea_id: str,
    partner: str,
    force: bool,
    fixtures_dir: Path | None,
) -> None:
    """Force a single-pair reconciliation and print the outcome."""

    asyncio.run(_run(lea_id, partner, force, fixtures_dir))


async def _run(
    lea_id: str,
    partner: str,
    force: bool,
    fixtures_dir: Path | None,
) -> None:
    factory = session_factory()
    snapshot_provider = make_snapshot_provider(
        fixtures_dir=fixtures_dir or default_fixtures_dir(),
        factory=factory,
    )
    scheduler = ReconciliationScheduler(
        session_factory=factory,
        reconciliation_service=ReconciliationService(
            session_factory=factory
        ),
        snapshot_provider=snapshot_provider,
    )

    report = await scheduler.reconcile_one(
        lea_id=LeaId(lea_id), partner=partner, force=force
    )
    _print_report(report)


def _print_report(report: ReconciliationReport) -> None:
    click.echo(f"lea_id            = {report.lea_id}")
    click.echo(f"partner           = {report.partner}")
    click.echo(f"status            = {report.status}")
    click.echo(f"started_at        = {report.started_at.isoformat()}")
    click.echo(f"completed_at      = {report.completed_at.isoformat()}")
    click.echo(f"canonical_root    = {report.canonical_root_hash}")
    click.echo(
        f"partner_root      = {report.partner_root_hash or '(skipped)'}"
    )
    if report.drift:
        click.echo("drift:")
        for d in report.drift:
            click.echo(f"  {d.entity_type}:")
            click.echo(
                f"    canonical_only = {list(d.canonical_only_ids)}"
            )
            click.echo(
                f"    partner_only   = {list(d.partner_only_ids)}"
            )


__all__ = ["reconcile_cmd"]
