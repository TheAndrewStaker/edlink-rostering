"""Active-alerts feed.

The admin app's top banner reads from here. Combines the three cross-LEA
evaluators (cursor-lag + quarantine-growth + reconciliation-drift) into
one stream so the UI does not need to call multiple endpoints to render
the banner.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.api.auth import Operator, require
from edlink_rostering.api.dependencies import (
    get_session_factory,
    get_telemetry,
)
from edlink_rostering.api.schemas import AlertOut
from edlink_rostering.infrastructure.ports import TelemetryFacade
from edlink_rostering.services.alerts import AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get(
    "", response_model=list[AlertOut], operation_id="alerts.list_active"
)
async def list_active_alerts(
    op: Operator = Depends(require("auditor")),
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    telemetry: TelemetryFacade = Depends(get_telemetry),
) -> list[AlertOut]:
    _ = op  # role gate; identity not used below
    service = AlertService(telemetry=telemetry)
    async with factory() as session:
        cursor = await service.evaluate_cursor_lag(session)
        quarantine = await service.evaluate_quarantine_growth(session)
        reconciliation = await service.evaluate_reconciliation_drift(session)
    out: list[AlertOut] = []
    for r in cursor + quarantine + reconciliation:
        out.append(
            AlertOut(
                code=r.code,
                severity=r.severity,
                dedup_key=r.dedup_key,
                lea_id=r.properties.get("lea_id"),
                measurements=r.measurements,
                properties=r.properties,
            )
        )
    return out
