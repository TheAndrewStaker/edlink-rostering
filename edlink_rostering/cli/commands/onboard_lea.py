"""``onboard-lea``: five-step interactive onboarding flow for a new LEA.

Drives the full first-touch sequence for a district arriving at the
platform:

1. **Create LEA**: insert a row in ``leas`` with ``status='onboarding'``.
   Idempotent via :class:`LeaAlreadyExists`; if the LEA already exists,
   the command continues with the existing row rather than failing.
2. **Stage EdLink token in Key Vault**: write the per-LEA bearer token
   to the mocked vault at ``edlink-token-<lea_id>`` so the connector's
   authorize step can find it. The token comes from ``--token`` or
   the ``EDLINK_TOKEN`` env var (the env path keeps the value off
   shell history).
3. **Authorize the connector**: call
   :meth:`EdLinkConnector.authorize_lea` so the per-LEA authorization
   appears in the partner-side roll-up.
4. **Initialize the cursor**: read ``get_latest_cursor`` and persist it
   so the next polling cycle starts at the most recent event in the
   retention window. Skipped under ``--skip-bulk-load`` for districts
   that have no roster fixture yet.
5. **Report entity counts**: walk the partner resources and print the
   per-entity-type counts the operator can verify before activating.

The ``--activate`` flag follows step 5 with a status transition to
``active`` so the LEA is immediately included in scheduled syncs.

Operator identity: the audit row's ``operator_id`` is resolved from
``--operator`` (a subject already in the ``operator`` table) or the
OS username's match in the operator table. The flow refuses to run if
no matching operator exists; this preserves the audit chain.
"""

from __future__ import annotations

import asyncio
import getpass
import uuid
from pathlib import Path

import click
from sqlalchemy import text

from edlink_rostering.cli._db import session_factory
from edlink_rostering.cli._snapshot import default_fixtures_dir
from edlink_rostering.connectors.edlink import EdLinkClient, EdLinkConnector
from edlink_rostering.connectors.protocol import AuthParams
from edlink_rostering.core.types import Cursor, LeaId
from edlink_rostering.infrastructure.azure_mocks.key_vault import KeyVaultClient
from edlink_rostering.services.lea_admin import (
    CreateLeaInput,
    InvalidStatusTransition,
    LeaAdminService,
    LeaAlreadyExists,
)


@click.command("onboard-lea")
@click.argument("lea_id")
@click.option("--name", required=True, help="District display name.")
@click.option(
    "--lea-type",
    default="traditional_district",
    show_default=True,
    help="LEA category (e.g. traditional_district, charter_lea).",
)
@click.option(
    "--state",
    required=True,
    help="Two-letter US state code (e.g. CA, WA).",
)
@click.option(
    "--timezone",
    default="America/New_York",
    show_default=True,
    help="IANA timezone identifier for the LEA.",
)
@click.option(
    "--nces-lea-id",
    default=None,
    help="Optional NCES LEA identifier.",
)
@click.option(
    "--edlink-integration-id",
    default=None,
    help="Optional EdLink integration id for the LEA.",
)
@click.option(
    "--token",
    default=None,
    envvar="EDLINK_TOKEN",
    help=(
        "EdLink bearer token to stage in Key Vault. Defaults to the"
        " EDLINK_TOKEN env var so the value stays out of shell history."
    ),
)
@click.option(
    "--operator",
    default=None,
    help=(
        "Operator subject already present in the operator table."
        " Defaults to the OS username."
    ),
)
@click.option(
    "--activate",
    is_flag=True,
    default=False,
    help="After step 5, transition the LEA from onboarding to active.",
)
@click.option(
    "--skip-bulk-load",
    is_flag=True,
    default=False,
    help=(
        "Skip cursor initialization + entity-count walk for an LEA"
        " whose fixture has not been authored yet."
    ),
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
def onboard_lea_cmd(
    lea_id: str,
    name: str,
    lea_type: str,
    state: str,
    timezone: str,
    nces_lea_id: str | None,
    edlink_integration_id: str | None,
    token: str | None,
    operator: str | None,
    activate: bool,
    skip_bulk_load: bool,
    fixtures_dir: Path | None,
) -> None:
    """Run the five-step LEA onboarding sequence.

    Idempotent: re-running with the same lea_id reuses the existing row.
    """

    asyncio.run(
        _run(
            lea_id=lea_id,
            name=name,
            lea_type=lea_type,
            state=state,
            timezone=timezone,
            nces_lea_id=nces_lea_id,
            edlink_integration_id=edlink_integration_id,
            token=token,
            operator_subject=operator or getpass.getuser(),
            activate=activate,
            skip_bulk_load=skip_bulk_load,
            fixtures_dir=fixtures_dir or default_fixtures_dir(),
        )
    )


async def _run(
    *,
    lea_id: str,
    name: str,
    lea_type: str,
    state: str,
    timezone: str,
    nces_lea_id: str | None,
    edlink_integration_id: str | None,
    token: str | None,
    operator_subject: str,
    activate: bool,
    skip_bulk_load: bool,
    fixtures_dir: Path,
) -> None:
    factory = session_factory()
    operator_id = await _resolve_operator_id(factory, operator_subject)
    service = LeaAdminService(session_factory=factory)
    lea = LeaId(lea_id)

    click.echo(f"Onboarding LEA {lea_id!r} as operator {operator_subject!r}.")

    # ── Step 1: create LEA ────────────────────────────────────────────
    existing = await service.get(lea)
    if existing is None:
        click.echo("Step 1/5: creating LEA row...")
        try:
            await service.create_lea(
                params=CreateLeaInput(
                    id=lea,
                    name=name,
                    lea_type=lea_type,
                    state=state,
                    timezone=timezone,
                    nces_lea_id=nces_lea_id,
                    edlink_integration_id=edlink_integration_id,
                ),
                operator_id=operator_id,
                reason=f"onboard-lea CLI by {operator_subject}",
            )
        except LeaAlreadyExists as exc:
            raise click.ClickException(str(exc)) from None
    else:
        click.echo(
            f"Step 1/5: LEA {lea_id!r} already exists"
            f" (status={existing.status}); continuing."
        )

    # ── Step 2: stage Key Vault token ─────────────────────────────────
    vault = KeyVaultClient()
    secret_name = f"edlink-token-{lea_id}"
    if token is not None:
        click.echo(f"Step 2/5: staging Key Vault secret {secret_name!r}...")
        vault.put_secret(secret_name, token)
    else:
        click.echo(
            f"Step 2/5: --token not provided; checking for existing"
            f" {secret_name!r} in Key Vault."
        )

    # ── Step 3: authorize the EdLink connector ────────────────────────
    click.echo("Step 3/5: authorizing EdLink connector for LEA...")
    connector = EdLinkConnector(
        client=EdLinkClient(fixtures_dir=fixtures_dir),
        key_vault=vault,
        session_factory=factory,
    )
    auth_result = await connector.authorize_lea(lea, AuthParams())
    if not auth_result.success:
        raise click.ClickException(
            "Connector authorization failed:"
            f" {auth_result.error or 'unknown error'}"
        )
    click.echo(f"  authorized; scopes={auth_result.scopes_granted}")

    # ── Step 4: initialize the cursor ────────────────────────────────
    if skip_bulk_load:
        click.echo("Step 4/5: --skip-bulk-load set; cursor untouched.")
    else:
        click.echo("Step 4/5: initializing cursor to latest event...")
        latest = await connector.get_latest_cursor(lea)
        await connector.set_cursor(lea, latest)
        click.echo(
            f"  cursor set to {latest.value or '(empty)'}"
            f" observed_at={latest.observed_at.isoformat() if latest.observed_at else '(none)'}"
        )

    # ── Step 5: report entity counts ─────────────────────────────────
    if skip_bulk_load:
        click.echo("Step 5/5: --skip-bulk-load set; entity counts skipped.")
    else:
        click.echo("Step 5/5: walking partner resources for entity counts...")
        snapshot = await connector.walk_resources(lea)
        for entity_type, rows in sorted(snapshot.items()):
            click.echo(f"  {entity_type}: {len(rows)}")

    # ── Optional activation ──────────────────────────────────────────
    if activate:
        click.echo("Activating LEA (onboarding -> active)...")
        try:
            await service.transition_status(
                lea_id=lea,
                target_status="active",
                operator_id=operator_id,
                reason=f"onboard-lea --activate by {operator_subject}",
            )
            click.echo("  LEA is now active.")
        except InvalidStatusTransition as exc:
            click.echo(f"  Skipping activation: {exc}")

    click.echo("Onboarding complete.")


async def _resolve_operator_id(
    factory: object, subject: str
) -> uuid.UUID:
    """Return the operator.id for ``subject`` or raise a CLI error.

    The audit chain depends on a real operator row; the CLI refuses to
    write an audit row tied to a non-existent operator.
    """

    async with factory() as session:  # type: ignore[operator]
        row = (
            await session.execute(
                text("SELECT id FROM operator WHERE subject = :s"),
                {"s": subject},
            )
        ).first()
    if row is None:
        raise click.ClickException(
            f"No operator with subject {subject!r} in the operator table."
            " Pass --operator <subject> or seed the operator first."
        )
    return row.id  # type: ignore[no-any-return]


__all__ = ["onboard_lea_cmd"]
