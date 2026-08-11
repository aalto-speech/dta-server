"""Cohort ranking against a real database.

Covers the tie behaviour, which used to be resolved by `guid ASC`: two learners with
identical averages were ranked one above the other permanently, by an accident of how
their identifiers happened to sort.
"""

# pylint: disable=redefined-outer-name

import dataclasses
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.analytics import ComparisonStats, GetCohortStatsInput
from app.models.onboarding import CreateUserInput
from app.models.speech_assessment import AssessmentCreateInput


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A real SQLite database on the production schema, wired into app.db."""

    import app.db as db_module

    path = tmp_path / "cohort.db"
    schema = (Path(db_module.__file__).with_name("schema.sql")).read_text(encoding="utf-8")

    def _connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(db_module, "_get_connection", _connect)

    with _connect() as conn:
        conn.executescript(schema)

    return db_module


def _small_cohorts(db, monkeypatch, min_cohort_size: int) -> None:
    """Let a handful of learners form a cohort. SETTINGS is a frozen dataclass."""

    monkeypatch.setattr(db, "SETTINGS", dataclasses.replace(
        db.SETTINGS, min_cohort_size=min_cohort_size, min_user_assessments=3,
    ))


def _learner(db, score: float, guid: str | None = None) -> str:
    """Create a user at A2 with three scored assessments, all at `score`."""

    value = guid or str(uuid4())
    db.create_user(CreateUserInput(
        guid=value,
        consent_accepted=True,
        consent_timestamp=datetime.now(timezone.utc),
        finnish_self_assessment="A2",
    ))
    for _ in range(3):
        db.create_assessment(AssessmentCreateInput(
            guid=value,
            task_id=1,
            audio_id=str(uuid4()),
            audio_path="/dev/null",
            transcript="hei",
            accuracy=score, fluency=score, proficiency=score,
            pronunciation=score, range_score=score,
        ))
    return value


def _cohort(db, scores: list[float]) -> list[str]:
    return [_learner(db, score) for score in scores]


def test_tied_learners_share_a_rank_regardless_of_guid(db, monkeypatch):
    """Two identical averages produce one rank, not two ordered by identifier.

    The guids are chosen so one sorts first and the other last; under the old
    `ORDER BY avg_score DESC, guid ASC` tiebreak they came back as #2 and #3.
    """

    _small_cohorts(db, monkeypatch, 3)

    _learner(db, 3.0)
    first = _learner(db, 2.0, guid="00000000-0000-4000-8000-000000000001")
    last = _learner(db, 2.0, guid="ffffffff-0000-4000-8000-00000000000f")

    ranks = {
        guid: db.get_cohort_stats(GetCohortStatsInput(guid=guid)).rank
        for guid in (first, last)
    }

    assert ranks[first] == ranks[last] == 2


def test_a_tie_group_does_not_shift_the_learners_below_it(db, monkeypatch):
    """Competition ranking: after two learners tie at #2, the next one is #4."""

    _small_cohorts(db, monkeypatch, 4)

    _learner(db, 3.0)
    _learner(db, 2.0)
    _learner(db, 2.0)
    lowest = _learner(db, 1.0)

    stats = db.get_cohort_stats(GetCohortStatsInput(guid=lowest))

    assert isinstance(stats, ComparisonStats)
    assert stats.rank == 4
    assert stats.cohort_size == 4


def test_top_learner_gets_rank_one_and_a_display_bucket(db, monkeypatch):
    _small_cohorts(db, monkeypatch, 4)

    best = _learner(db, 3.5)
    _cohort(db, [3.0, 2.0, 1.0])

    stats = db.get_cohort_stats(GetCohortStatsInput(guid=best))

    assert stats.rank == 1
    assert stats.display.top_rank == 1
    assert stats.display.top_percent == 25   # 1 of 4 is top 25%, not top 1%


def test_bottom_half_learner_is_given_nothing_to_display(db, monkeypatch):
    _small_cohorts(db, monkeypatch, 4)

    _cohort(db, [3.5, 3.0, 2.0])
    worst = _learner(db, 1.0)

    stats = db.get_cohort_stats(GetCohortStatsInput(guid=worst))

    assert stats.rank == 4
    assert stats.display.top_rank is None
    assert stats.display.top_percent is None
