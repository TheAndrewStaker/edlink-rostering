"""Retry service: rewind the cursor and audit a retry of a failed sync.

The retry path is deliberately narrow. The CLI does not re-run the
connector or push a Service Bus message itself. It does three things:

1. Loads the target ``sync_jobs`` row. Refuses if the sync was a
   ``revert`` synthetic. Refuses if the status is ``success`` and the
   caller did not pass ``forced=True`` (a successful sync should not be
   replayed except as a deliberate operator action).
2. Rewinds ``cursor_state.last_event_id`` for the LEA + partner to the
   ``cursor_before`` value of the target sync_job, clears
   ``last_event_at`` so the lag clock restarts from the moment of the
   actual partner replay rather than the moment of retry.
3. Writes a ``retry_actions`` audit row capturing the operator, the
   reason, the cursor that was rewound to, and whether the retry was
   forced.

Production flow: the next scheduled poll picks the LEA up with the
rewound cursor and replays the events. The sync worker is idempotent
per :mod:`source_event_id` on snapshots, so a retry of a partially
applied sync still produces a clean final state.

For the POC demo and tests the caller can run :meth:`drain_lea` on the
worker after the retry to immediately exercise the replay path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edlink_rostering.core.types import LeaId


@dataclass(frozen=True)
class RetryOutcome:
    """Result of one retry call.

    ``cursor_rewound_to`` is the cursor value the LEA's ``cursor_state``
    was reset to. ``forced`` reflects whether the operator overrode the
    "non-failed" guard.
    """

    retry_id: uuid.UUID
    sync_job_id: uuid.UUID
    lea_id: LeaId
    partner: str
    cursor_rewound_to: str | None
    forced: bool


class RetryError(RuntimeError):
    """Base for retry refusals."""


class RetrySyncJobNotFound(RetryError):
    """The named sync_job_id does not exist."""


class RetryRefused(RetryError):
    """The retry guards refused the action. Pass ``forced=True`` to
    override after operator confirmation."""


class RetryService:
    """Operator-driven retry of a sync_job.

    Connects via ``session_factory`` (typically ``ops_session_factory``).
    Tests substitute a per-test session factory.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory

    async def retry(
        self,
        *,
        sync_job_id: uuid.UUID,
        operator_identity: str,
        reason: str,
        forced: bool = False,
    ) -> RetryOutcome:
        """Rewind the cursor and write the audit row.

        Refuses on a ``revert`` synthetic row always (you cannot replay
        a revert; you write a new sync). Refuses on ``success`` unless
        ``forced=True``. ``running`` is allowed without force: the
        retry path is also how operators recover from a stalled
        in-flight sync.
        """

        async with self._sessions() as session:
            now = datetime.now(UTC)
            target = await self._load_sync_job(session, sync_job_id)

            if target.status == "revert":
                raise RetryRefused(
                    f"sync_job_id {sync_job_id} is a revert synthetic row; "
                    "write a new sync instead of retrying a revert."
                )
            if target.status == "success" and not forced:
                raise RetryRefused(
                    f"sync_job_id {sync_job_id} ended in status='success'. "
                    "Pass forced=True (CLI: --force) to retry a successful "
                    "sync."
                )

            cursor_rewound_to: str | None = target.cursor_before
            await self._rewind_cursor(
                session=session,
                lea_id=LeaId(target.lea_id),
                partner=target.partner,
                cursor_value=cursor_rewound_to,
                now=now,
            )
            retry_id = await self._insert_retry_action(
                session=session,
                sync_job_id=sync_job_id,
                lea_id=LeaId(target.lea_id),
                partner=target.partner,
                operator_identity=operator_identity,
                reason=reason,
                retried_at=now,
                cursor_rewound_to=cursor_rewound_to,
                forced=forced,
            )
            await session.commit()

            return RetryOutcome(
                retry_id=retry_id,
                sync_job_id=sync_job_id,
                lea_id=LeaId(target.lea_id),
                partner=target.partner,
                cursor_rewound_to=cursor_rewound_to,
                forced=forced,
            )

    async def _load_sync_job(
        self, session: AsyncSession, sync_job_id: uuid.UUID
    ) -> Any:
        row = (
            await session.execute(
                text(
                    """
                    SELECT lea_id, partner, status, cursor_before
                    FROM sync_jobs WHERE id = :id
                    """
                ),
                {"id": sync_job_id},
            )
        ).first()
        if row is None:
            raise RetrySyncJobNotFound(
                f"sync_job_id {sync_job_id} not found"
            )
        return row

    async def _rewind_cursor(
        self,
        *,
        session: AsyncSession,
        lea_id: LeaId,
        partner: str,
        cursor_value: str | None,
        now: datetime,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO cursor_state (
                    lea_id, partner, last_event_id, last_event_at,
                    last_poll_at, cold_start_required, updated_at
                ) VALUES (
                    :lea_id, :partner, :last_event_id, NULL,
                    NULL, false, :updated_at
                )
                ON CONFLICT (lea_id, partner) DO UPDATE SET
                    last_event_id = EXCLUDED.last_event_id,
                    last_event_at = NULL,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "lea_id": lea_id,
                "partner": partner,
                "last_event_id": cursor_value,
                "updated_at": now,
            },
        )

    async def _insert_retry_action(
        self,
        *,
        session: AsyncSession,
        sync_job_id: uuid.UUID,
        lea_id: LeaId,
        partner: str,
        operator_identity: str,
        reason: str,
        retried_at: datetime,
        cursor_rewound_to: str | None,
        forced: bool,
    ) -> uuid.UUID:
        retry_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO retry_actions (
                    id, sync_job_id, lea_id, partner, operator_identity,
                    reason, retried_at, cursor_rewound_to, forced
                ) VALUES (
                    :id, :sync_job_id, :lea_id, :partner, :operator,
                    :reason, :retried_at, :cursor_rewound_to, :forced
                )
                """
            ),
            {
                "id": retry_id,
                "sync_job_id": sync_job_id,
                "lea_id": lea_id,
                "partner": partner,
                "operator": operator_identity,
                "reason": reason,
                "retried_at": retried_at,
                "cursor_rewound_to": cursor_rewound_to,
                "forced": forced,
            },
        )
        return retry_id


__all__ = [
    "RetryError",
    "RetryOutcome",
    "RetryRefused",
    "RetryService",
    "RetrySyncJobNotFound",
]
