"""Tests for the retention-policy drift detection.

The drift job at edlink_rostering.services.retention_drift diffs a checked-in
snapshot of EdLink's v2.0 events overview page against the live page.
The snapshot lives at fixtures/edlink/retention-policy-snapshot.txt and
is captured against the v2.0 wire format (``date``, ``$after``/``$first``/
``$next``, ``materialization_id``).

These tests exercise the diff logic against synthetic fetched text so
the test suite does not depend on live network access. The production
job will wire an httpx fetch in as the ``fetcher`` callable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edlink_rostering.services.retention_drift import (
    check_drift,
    load_snapshot_phrases,
    normalize,
)


SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "edlink"
    / "retention-policy-snapshot.txt"
)


def test_snapshot_file_exists_and_loads_phrases() -> None:
    """The snapshot file ships with the fixture set and has parseable phrases."""

    phrases = load_snapshot_phrases(SNAPSHOT_PATH)
    assert len(phrases) >= 5
    # The load-bearing 30-day-retention claim is the most important one to
    # detect drift on; pin its presence explicitly.
    assert any("30 days" in p for p in phrases)


def test_normalize_collapses_whitespace_and_lowercases() -> None:
    raw = "  Events  API   retention:\n\n  30  Days  "
    assert normalize(raw) == "events api retention: 30 days"


def test_normalize_drops_comment_lines() -> None:
    raw = "# this is a comment\nreal content here\n# trailing comment"
    assert normalize(raw) == "real content here"


def test_check_drift_passes_when_fetched_contains_all_phrases() -> None:
    """The snapshot's phrases all appear in the fetched text, no drift."""

    # The fetcher returns the snapshot itself; every claim is present.
    fetched = SNAPSHOT_PATH.read_text(encoding="utf-8")
    report = check_drift(SNAPSHOT_PATH, lambda: fetched)
    assert report.drifted is False
    assert report.missing_phrases == ()


def test_check_drift_fires_when_30_day_window_disappears() -> None:
    """The load-bearing 30-day claim is the canonical drift trigger."""

    # Synthesize a fetched page where the retention claim is missing.
    fetched_without_retention = "Events API: this page covers polling."
    report = check_drift(SNAPSHOT_PATH, lambda: fetched_without_retention)
    assert report.drifted is True
    assert any("30 days" in p for p in report.missing_phrases)


def test_check_drift_tolerates_added_prose() -> None:
    """Cosmetic additions on the upstream side do not fire drift.

    EdLink could add a new "Best practices" section to the page; as long
    as every claim our snapshot tracks is still present, we do not page.
    """

    snapshot_text = SNAPSHOT_PATH.read_text(encoding="utf-8")
    fetched = snapshot_text + "\n\nBest practices: be patient with retries."
    report = check_drift(SNAPSHOT_PATH, lambda: fetched)
    assert report.drifted is False


def test_check_drift_fires_when_payload_shape_claim_disappears() -> None:
    """The payload-shape phrase is also load-bearing.

    If EdLink renames `date` to `timestamp` (a wire-format break),
    the phrase that names the field disappears from the live page and
    the drift fires.
    """

    # Synthesize a page that mentions retention but renames the field.
    fetched = (
        "events api retention: 30 days. event payload shape: each event"
        " has the fields id, timestamp, type, and data."
    )
    report = check_drift(SNAPSHOT_PATH, lambda: fetched)
    assert report.drifted is True
    assert any(
        "materialization_id" in p for p in report.missing_phrases
    ), "The v2.0 payload-shape phrase naming materialization_id should be flagged."


@pytest.mark.parametrize(
    "missing_keyword",
    ["$after", "pagination", "authorization header"],
)
def test_drift_catches_each_load_bearing_topic(missing_keyword: str) -> None:
    """Each load-bearing topic in the snapshot has a phrase that names it.

    This is a sanity check that the snapshot is structured around the
    contract surfaces the design depends on, not a pile of trivia.
    """

    phrases = load_snapshot_phrases(SNAPSHOT_PATH)
    assert any(
        missing_keyword in p for p in phrases
    ), f"Snapshot is missing a phrase that mentions {missing_keyword!r}."
