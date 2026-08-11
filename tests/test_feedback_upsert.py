# pylint: disable=redefined-outer-name

"""One learner answering one question about one recording is ONE row.

The 2.0.0 client posts each answer as it is given -- the rating when the emoji is tapped,
the comment when the learner leaves the text box -- so a learner who changes their mind
posts several times. Those are the same answer at different moments, not several opinions.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.feedback import CreateFeedbackInput
from app.models.onboarding import CreateUserInput
from app.models.speech_assessment import AssessmentCreateInput


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A real SQLite database on the production schema, wired into app.db."""

    import app.db as db_module

    path = tmp_path / "feedback.db"
    schema = (Path(db_module.__file__).with_name("schema.sql")).read_text(encoding="utf-8")

    def _connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(db_module, "_get_connection", _connect)

    with _connect() as conn:
        conn.executescript(schema)

    return db_module


@pytest.fixture
def guid(db):
    value = str(uuid4())
    db.create_user(CreateUserInput(
        guid=value,
        consent_accepted=True,
        consent_timestamp=datetime.now(timezone.utc),
        finnish_self_assessment="A1",
    ))
    return value


@pytest.fixture
def assessment_id(db, guid):
    return db.create_assessment(AssessmentCreateInput(
        guid=guid,
        task_id=1,
        audio_id=str(uuid4()),
        audio_path="/dev/null",
        transcript="hei",
        accuracy=2.0, fluency=2.0, proficiency=2.0, pronunciation=2.0, range_score=2.0,
    ))


def _rows(db, guid):
    with db.database() as conn:
        return conn.execute(
            "SELECT type, reaction_value, comment FROM feedback WHERE guid = ? "
            "ORDER BY id",
            (guid,),
        ).fetchall()


def test_changing_your_mind_updates_the_row_instead_of_adding_one(
    db, guid, assessment_id,
):
    """Tap an emoji, type a comment, change the emoji: three posts, one row."""

    for value, comment in ((3, None), (3, "was ok"), (5, "was ok")):
        db.create_feedback(CreateFeedbackInput(
            guid=guid,
            assessment_id=assessment_id,
            feedback_classification="result_accuracy",
            reaction_value=value,
            comment=comment,
        ))

    assert _rows(db, guid) == [("result_accuracy", 5, "was ok")]


def test_the_three_assessment_types_are_deduplicated_independently(
    db, guid, assessment_id,
):
    """Different questions about the same recording are different answers."""

    for kind in ("self_assessment", "result_accuracy", "result_understanding"):
        for value in (1, 4):
            db.create_feedback(CreateFeedbackInput(
                guid=guid,
                assessment_id=assessment_id,
                feedback_classification=kind,
                reaction_value=value,
            ))

    rows = _rows(db, guid)
    assert len(rows) == 3
    assert {r[0] for r in rows} == {
        "self_assessment", "result_accuracy", "result_understanding"}
    assert {r[1] for r in rows} == {4}


def test_the_same_question_about_a_different_recording_is_a_different_answer(db, guid):
    """The dedup key includes assessment_id, so task 2 does not overwrite task 1."""

    first = db.create_assessment(AssessmentCreateInput(
        guid=guid, task_id=1, audio_id=str(uuid4()), audio_path="/dev/null",
        transcript="a", accuracy=2.0, fluency=2.0, proficiency=2.0,
        pronunciation=2.0, range_score=2.0))
    second = db.create_assessment(AssessmentCreateInput(
        guid=guid, task_id=2, audio_id=str(uuid4()), audio_path="/dev/null",
        transcript="b", accuracy=2.0, fluency=2.0, proficiency=2.0,
        pronunciation=2.0, range_score=2.0))

    for assessment in (first, second):
        db.create_feedback(CreateFeedbackInput(
            guid=guid, assessment_id=assessment,
            feedback_classification="result_accuracy", reaction_value=2))

    assert len(_rows(db, guid)) == 2


def test_app_scoped_types_are_left_alone(db, guid):
    """`comparison_ui` and `overall_experience` carry no assessment_id and are sent once.

    SQLite treats NULLs as distinct in a unique index, so they fall through to a plain
    insert. Asserted because it is a property of SQLite rather than of our SQL, and a
    future migration to a database with different NULL semantics would silently change it.
    """

    for _ in range(2):
        db.create_feedback(CreateFeedbackInput(
            guid=guid, feedback_classification="comparison_ui", reaction_value=4))

    assert len(_rows(db, guid)) == 2


def test_a_cleared_comment_is_stored_as_cleared(db, guid, assessment_id):
    """The client sends the whole answer every time, so an absent comment means the
    learner deleted it -- not that they left the old one alone."""

    db.create_feedback(CreateFeedbackInput(
        guid=guid, assessment_id=assessment_id,
        feedback_classification="result_accuracy", reaction_value=3, comment="typo"))
    db.create_feedback(CreateFeedbackInput(
        guid=guid, assessment_id=assessment_id,
        feedback_classification="result_accuracy", reaction_value=3, comment=None))

    assert _rows(db, guid) == [("result_accuracy", 3, None)]


def test_created_at_survives_an_update_and_updated_at_moves(db, guid, assessment_id):
    """Both timestamps are kept: when the question was first answered, and when the
    learner settled on the answer."""

    db.create_feedback(CreateFeedbackInput(
        guid=guid, assessment_id=assessment_id,
        feedback_classification="result_accuracy", reaction_value=1))

    with db.database() as conn:
        conn.execute(
            "UPDATE feedback SET created_at = '2020-01-01 00:00:00', "
            "updated_at = '2020-01-01 00:00:00' WHERE guid = ?", (guid,))

    db.create_feedback(CreateFeedbackInput(
        guid=guid, assessment_id=assessment_id,
        feedback_classification="result_accuracy", reaction_value=5))

    with db.database() as conn:
        created, updated = conn.execute(
            "SELECT created_at, updated_at FROM feedback WHERE guid = ?",
            (guid,),
        ).fetchone()

    assert created == "2020-01-01 00:00:00"
    assert updated > created
