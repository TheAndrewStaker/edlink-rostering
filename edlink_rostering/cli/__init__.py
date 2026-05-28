"""Operator CLI entry point.

Ten commands ship as of session 7: ``list-syncs``, ``show-sync``,
``revert``, ``retry``, ``cursor-status``, ``list-quarantine``,
``release-quarantine``, ``reject-quarantine``, ``reconcile``,
``reconcile-sweep``. The CLI process connects to Postgres as
``edlink_ops`` and reads/writes the operator-facing surface. The
sync worker, which connects as ``edlink_app``, is invoked separately
by the demo runner or by Service Bus message dispatch in production.

Adding a new command: drop a module under ``edlink_rostering/cli/commands/``
and register it via :func:`register_commands`. The commands here are
deliberately split into small modules so each one is self-contained
and easy to extend without the file growing past the 500-LOC guidance
in the global conventions.
"""

from __future__ import annotations

import click

from edlink_rostering.cli.commands.cursor_status import cursor_status
from edlink_rostering.cli.commands.list_syncs import list_syncs
from edlink_rostering.cli.commands.onboard_lea import onboard_lea_cmd
from edlink_rostering.cli.commands.quarantine_cmds import (
    list_quarantine,
    reject_quarantine,
    release_quarantine,
)
from edlink_rostering.cli.commands.reconcile_cmd import reconcile_cmd
from edlink_rostering.cli.commands.reconcile_sweep_cmd import reconcile_sweep_cmd
from edlink_rostering.cli.commands.retry_cmd import retry_cmd
from edlink_rostering.cli.commands.revert_cmd import revert_cmd
from edlink_rostering.cli.commands.show_sync import show_sync


@click.group(help="Operator CLI for the EdLink rostering framework.")
def cli() -> None:
    """Root group. Each subcommand is defined in its own module under
    ``edlink_rostering/cli/commands/``."""


def register_commands() -> None:
    """Attach the shipped commands. Imported by ``cli`` automatically
    on first use; called explicitly so tests can construct the CLI fresh
    without relying on module-level side effects."""

    cli.add_command(list_syncs)
    cli.add_command(show_sync)
    cli.add_command(revert_cmd, name="revert")
    cli.add_command(retry_cmd, name="retry")
    cli.add_command(cursor_status)
    cli.add_command(list_quarantine)
    cli.add_command(release_quarantine)
    cli.add_command(reject_quarantine)
    cli.add_command(reconcile_cmd)
    cli.add_command(reconcile_sweep_cmd)
    cli.add_command(onboard_lea_cmd)


register_commands()


__all__ = ["cli"]
