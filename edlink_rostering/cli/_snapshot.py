"""Shared partner-snapshot provider for the reconciliation CLI commands.

``reconcile`` and ``reconcile-sweep`` both need a callable that maps
(partner, lea_id) to the partner-side projection of canonical state.
Production wires every supported partner here; the POC ships with
EdLink only.

Centralizing the dispatch keeps the two CLI commands focused on their
own argument parsing and output formatting, and gives any future
command (a `walk-resources` debug printer, for example) one place to
reach for the same wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.core.types import LeaId
from edlink_rostering.infrastructure.azure_mocks.key_vault import KeyVaultClient
from edlink_rostering.services.reconciliation_scheduler import SnapshotProvider


def default_fixtures_dir() -> Path:
    """``fixtures/edlink`` resolved from the CLI module location.

    Stable regardless of the operator's cwd because the path resolves
    off this module's __file__.
    """

    return Path(__file__).parent.parent.parent / "fixtures" / "edlink"


def make_snapshot_provider(
    *,
    fixtures_dir: Path,
    factory: Any,
) -> SnapshotProvider:
    """Build the partner-dispatch snapshot provider.

    EdLink wires :meth:`EdLinkConnector.walk_resources`. Any other
    partner raises a clean CLI error so the operator knows the
    POC-vs-production support gap. Adding a partner is a single
    branch here plus the new connector implementation.
    """

    vault = KeyVaultClient()
    edlink_connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=fixtures_dir),
        key_vault=vault,
        session_factory=factory,
    )

    async def provider(
        partner: str, lea_id: LeaId
    ) -> dict[str, list[dict[str, Any]]]:
        if partner == "edlink":
            return await edlink_connector.walk_resources(lea_id)
        raise click.ClickException(
            f"No walk_resources wired for partner {partner!r}."
            f" Only 'edlink' is supported in the POC."
        )

    return provider


__all__ = ["default_fixtures_dir", "make_snapshot_provider"]
