"""Retention-policy drift detection.

The 30-day Events API retention window is a load-bearing assumption
behind the cursor-lag alert at 20 days, the cold-start path, and the
disaster-recovery framework. A silent change to the window (or to the
payload shape or pagination contract) from EdLink would invalidate
all three.

The design doc § "Retention-drift detection" calls for two layers:

1. Contractual notification from EdLink as part of the partner contract.
2. Synthetic: a monthly job that fetches the live
   https://ed.link/docs/api/v2.0/events/overview page, normalizes it,
   and diffs it against a checked-in snapshot of the load-bearing
   claims.

This module implements layer 2. The fetcher is intentionally injected
so the test exercises the diff logic without a live network call, and
so a future Azure Function timer trigger can wire in a real httpx
fetch without changing the diff path.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


# Whitespace-collapse + lowercase is the normalization contract. The
# snapshot file documents the same rules so a future re-capture stays
# diff-stable. Comment lines (starting with '#') are dropped before
# the diff so editorial annotations in the snapshot do not show as
# drift against a fetched page that has no comments.
_WHITESPACE_RUN = re.compile(r"\s+")


@dataclass(frozen=True)
class DriftReport:
    """Outcome of one drift-check run.

    ``drifted`` is the only field the alert evaluator cares about.
    ``missing_phrases`` and ``unexpected_phrases`` are populated for
    operator inspection so the dashboard can show what changed.
    """

    drifted: bool
    snapshot_phrases: tuple[str, ...]
    fetched_normalized: str
    missing_phrases: tuple[str, ...]


def normalize(text: str) -> str:
    """Apply the normalization rules documented in the snapshot file."""

    stripped = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    return _WHITESPACE_RUN.sub(" ", stripped.lower()).strip()


def load_snapshot_phrases(snapshot_path: Path) -> tuple[str, ...]:
    """Read the snapshot file and return its load-bearing-claim phrases.

    The snapshot is organized as one claim per paragraph. Comment
    lines starting with ``#`` are dropped. Each non-empty paragraph
    after normalization is one phrase.
    """

    raw = snapshot_path.read_text(encoding="utf-8")
    cleaned = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    paragraphs = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
    return tuple(_WHITESPACE_RUN.sub(" ", p.lower()).strip() for p in paragraphs)


def check_drift(
    snapshot_path: Path,
    fetcher: Callable[[], str],
) -> DriftReport:
    """Compare the snapshot's load-bearing phrases against fetched text.

    A phrase counts as present when its normalized form appears as a
    substring of the normalized fetched text. The diff is asymmetric on
    purpose: cosmetic additions on EdLink's side (a new prose section
    that does not contradict our claims) do not fire drift; only
    disappearance or contradiction of a load-bearing claim does.

    ``fetcher`` returns the live page's text. In tests this is a
    fixture-backed callable; in production an httpx GET against the
    page URL.
    """

    phrases = load_snapshot_phrases(snapshot_path)
    fetched = normalize(fetcher())
    missing = tuple(p for p in phrases if p not in fetched)
    return DriftReport(
        drifted=bool(missing),
        snapshot_phrases=phrases,
        fetched_normalized=fetched,
        missing_phrases=missing,
    )


__all__ = ["DriftReport", "check_drift", "load_snapshot_phrases", "normalize"]
