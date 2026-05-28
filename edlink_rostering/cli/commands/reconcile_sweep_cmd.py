"""``reconcile-sweep``: run the daily reconciliation sweep on demand.

Wraps :meth:`edlink_rostering.services.reconciliation_scheduler.ReconciliationScheduler.run_daily_sweep`.
Production cadence is a timer-triggered Azure Function at 02:00
LEA-local. This command lets an operator drive the same path from
the CLI when investigating drift or warming up the reconciliation_runs
audit history.

Output mirrors the structure an operator pastes into a postmortem:
aggregate counts followed by the per-LEA breakdown and the failure
list. The Azure Monitor alert that fires on failures reads the same
SweepReport, so the CLI is the operator-facing analogue of what the
on-call runbook sees.
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
from edlink_rostering.services.reconciliation import ReconciliationService
from edlink_rostering.services.reconciliation_scheduler import (
    ReconciliationScheduler,
    SweepReport,
)


@click.command("reconcile-sweep")
@click.option(
    "--fixtures-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "EdLink fixtures directory. Defaults to fixtures/edlink"
        " relative to the CLI module."
    ),
)
def reconcile_sweep_cmd(fixtures_dir: Path | None) -> None:
    """Run the daily reconciliation sweep on demand and print the report."""

    asyncio.run(_run(fixtures_dir))


async def _run(fixtures_dir: Path | None) -> None:
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

    report = await scheduler.run_daily_sweep()
    _print_report(report)


def _print_report(report: SweepReport) -> None:
    elapsed = (report.completed_at - report.started_at).total_seconds()
    click.echo(f"started_at        = {report.started_at.isoformat()}")
    click.echo(f"completed_at      = {report.completed_at.isoformat()}")
    click.echo(f"elapsed_seconds   = {elapsed:.2f}")
    click.echo(f"total             = {report.total_authorizations}")
    click.echo(f"  matched         = {report.matched_count}")
    click.echo(f"  drift_detected  = {report.drift_count}")
    click.echo(f"  skipped         = {report.skipped_count}")
    click.echo(f"  failed          = {report.failed_count}")
    if report.per_lea:
        click.echo("per_lea:")
        for r in report.per_lea:
            click.echo(
                f"  {r.lea_id:<28}  {r.partner:<10}  {r.status}"
            )
    if report.failures:
        click.echo("failures:")
        for lea_id, partner, msg in report.failures:
            click.echo(f"  {lea_id:<28}  {partner:<10}  {msg}")


__all__ = ["reconcile_sweep_cmd"]
