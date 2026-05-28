"""Idempotency-Key replay service.

Implements the ``Idempotency-Key`` header contract on mutation
endpoints. When an operator includes the header, the service stores
the response after the handler runs and replays it on subsequent
requests with the same key. Same-key+different-body returns a 422
(Stripe-style strict mode); same-key while the first request is still
running returns a 409.

The header is optional. When absent, :func:`with_idempotency` simply
runs the handler and returns its result, so callers compose cleanly
whether or not the client opted in.

Storage is per-operator (the JWT subject), so two operators with
colliding key choices do not interfere. The route string is the
endpoint identifier (e.g. ``actions.retry_sync``); path parameters
are folded into the ``request_hash`` so the same key on two different
sync_jobs is distinguishable as a body mismatch.

Rows older than 24 hours are eligible for deletion by
:meth:`IdempotencyService.sweep_stale`. The
``ReconciliationScheduler.run_daily_sweep`` entry point invokes it as
part of the daily maintenance pass; CLI callers and tests can invoke
it directly.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TModel = TypeVar("TModel", bound=BaseModel)


class IdempotencyConflict(Exception):
    """The Idempotency-Key was used before with a different request body."""


class IdempotencyInFlight(Exception):
    """An earlier request with this Idempotency-Key is still running."""


@dataclass(frozen=True)
class IdempotencyReplay:
    """Cached response for an Idempotency-Key replay."""

    response_status: int
    response_body: dict[str, Any]


def _compute_request_hash(*, path: str, body: BaseModel | None) -> str:
    """Stable hash over the request path plus the body.

    Path is included so the same key on different resources (e.g. two
    different ``sync_job_id``s in the URL) is detected as a mismatch.
    Empty body hashes the path alone.
    """

    payload: dict[str, object] = {"path": path}
    if body is not None:
        payload["body"] = body.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyService:
    """Postgres-backed idempotency dedup store.

    Two-stage write:

    1. :meth:`get_or_register` inserts a pending row (or returns the
       cached replay). It is atomic via ``INSERT ... ON CONFLICT DO
       NOTHING``; the loser of an insert race sees the existing row
       and either replays (if complete) or raises
       :class:`IdempotencyInFlight` (if pending).
    2. :meth:`store_response` fills the pending row with the response
       after the handler runs.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def get_or_register(
        self,
        *,
        operator_id: uuid.UUID,
        route: str,
        key: str,
        request_hash: str,
    ) -> IdempotencyReplay | None:
        """Reserve the (operator_id, route, key) slot.

        Returns ``None`` when this is the first request for the key
        (caller proceeds with the handler and then calls
        :meth:`store_response`). Returns an :class:`IdempotencyReplay`
        when the key has already been used with the same body and the
        prior response is cached. Raises
        :class:`IdempotencyConflict` for same-key+different-body and
        :class:`IdempotencyInFlight` for same-key+pending.
        """

        async with self._factory() as session:
            inserted = (
                await session.execute(
                    text(
                        """
                        INSERT INTO idempotency_keys
                            (operator_id, route, key, request_hash)
                        VALUES (:op, :route, :key, :hash)
                        ON CONFLICT (operator_id, route, key) DO NOTHING
                        RETURNING operator_id
                        """
                    ),
                    {
                        "op": operator_id,
                        "route": route,
                        "key": key,
                        "hash": request_hash,
                    },
                )
            ).first()

            if inserted is not None:
                await session.commit()
                return None

            existing = (
                await session.execute(
                    text(
                        """
                        SELECT request_hash, response_status, response_body
                        FROM idempotency_keys
                        WHERE operator_id = :op
                          AND route = :route
                          AND key = :key
                        """
                    ),
                    {"op": operator_id, "route": route, "key": key},
                )
            ).first()
            await session.commit()

        if existing is None:
            # Race: row deleted between INSERT and SELECT. Treat as a
            # first-time reservation by recursing once; the recursion
            # is bounded because the deletion path is the daily sweep
            # and not user-initiated.
            return await self.get_or_register(
                operator_id=operator_id,
                route=route,
                key=key,
                request_hash=request_hash,
            )

        if existing.request_hash != request_hash:
            raise IdempotencyConflict(
                f"Idempotency-Key {key!r} was previously used on this"
                " route with a different request body."
            )

        if existing.response_status is None:
            raise IdempotencyInFlight(
                f"An earlier request with Idempotency-Key {key!r} is still"
                " running on this route. Retry shortly."
            )

        return IdempotencyReplay(
            response_status=int(existing.response_status),
            response_body=dict(existing.response_body or {}),
        )

    async def discard(
        self,
        *,
        operator_id: uuid.UUID,
        route: str,
        key: str,
    ) -> None:
        """Delete a pending row when the handler raised.

        Without this, a failed handler would leave the row pending and
        an operator who corrects the underlying issue and retries with
        the same key would hit :class:`IdempotencyInFlight` until the
        24h sweep. Deleting on failure preserves the "Idempotency-Key
        replays SUCCESS" contract and keeps retry-after-failure usable.
        """

        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    DELETE FROM idempotency_keys
                    WHERE operator_id = :op
                      AND route = :route
                      AND key = :key
                      AND response_status IS NULL
                    """
                ),
                {"op": operator_id, "route": route, "key": key},
            )
            await session.commit()

    async def sweep_stale(
        self,
        *,
        older_than: timedelta = timedelta(hours=24),
    ) -> int:
        """Delete rows whose ``created_at`` is older than ``older_than``.

        Bounds the table size per ADR-008's revisit triggers. The
        24-hour default matches the Stripe Idempotency-Key retention
        guidance and the budget documented in V0008's migration
        comment. Returns the number of rows deleted so callers (the
        scheduler, telemetry) can record the sweep outcome.

        Uses the ``ix_idempotency_keys_created_at`` index so the
        delete stays cheap even as the table grows. Both pending and
        completed rows are eligible once they age past the threshold;
        a stuck-pending row that survives a process crash gets
        reclaimed by the same sweep.
        """

        async with self._factory() as session:
            result = await session.execute(
                text(
                    """
                    DELETE FROM idempotency_keys
                    WHERE created_at < NOW() - (:hours * INTERVAL '1 hour')
                    """
                ),
                {"hours": older_than.total_seconds() / 3600.0},
            )
            await session.commit()
        rowcount = getattr(result, "rowcount", None)
        return int(rowcount) if rowcount is not None else 0

    async def count_rows(self) -> int:
        """Total row count, for the table-size alert."""

        async with self._factory() as session:
            row = (
                await session.execute(
                    text("SELECT COUNT(*) AS n FROM idempotency_keys")
                )
            ).first()
        return int(row.n) if row is not None else 0

    async def store_response(
        self,
        *,
        operator_id: uuid.UUID,
        route: str,
        key: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        """Complete a pending row with the handler's response."""

        async with self._factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE idempotency_keys
                    SET response_status = :status,
                        response_body = CAST(:body AS JSONB),
                        completed_at = now()
                    WHERE operator_id = :op
                      AND route = :route
                      AND key = :key
                    """
                ),
                {
                    "op": operator_id,
                    "route": route,
                    "key": key,
                    "status": response_status,
                    "body": json.dumps(response_body, default=str),
                },
            )
            await session.commit()


async def with_idempotency(
    *,
    factory: async_sessionmaker[AsyncSession],
    operator_id: uuid.UUID,
    route: str,
    path: str,
    idempotency_key: str | None,
    request_body: BaseModel | None,
    response_model: type[TModel],
    handler: Callable[[], Awaitable[TModel]],
) -> TModel:
    """Wrap a handler with optional Idempotency-Key replay.

    Behavior:

    * ``idempotency_key is None``: runs ``handler()`` and returns its
      result. No row is written.
    * Key set, first time seen: reserves the row, runs ``handler()``,
      stores the result, returns it.
    * Key set, previously seen with the same body: returns the cached
      response (no handler invocation, no side effects).
    * Key set, previously seen with a different body: raises
      :class:`IdempotencyConflict` (mapped to 422 by the global handler).
    * Key set, an earlier request still pending: raises
      :class:`IdempotencyInFlight` (mapped to 409).

    The ``path`` argument is folded into the request hash so the same
    key on different resources is detected as a mismatch rather than a
    silent replay of the wrong response.
    """

    if idempotency_key is None:
        return await handler()

    service = IdempotencyService(factory)
    request_hash = _compute_request_hash(path=path, body=request_body)

    replay = await service.get_or_register(
        operator_id=operator_id,
        route=route,
        key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return response_model.model_validate(replay.response_body)

    try:
        result = await handler()
    except Exception:
        # The handler raised. Clear the pending row so the operator can
        # correct the issue and retry with the same key.
        await service.discard(
            operator_id=operator_id,
            route=route,
            key=idempotency_key,
        )
        raise

    await service.store_response(
        operator_id=operator_id,
        route=route,
        key=idempotency_key,
        response_status=200,
        response_body=result.model_dump(mode="json"),
    )
    return result


__all__ = [
    "IdempotencyConflict",
    "IdempotencyInFlight",
    "IdempotencyReplay",
    "IdempotencyService",
    "with_idempotency",
]
