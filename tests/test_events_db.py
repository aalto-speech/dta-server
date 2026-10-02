# pylint: disable=redefined-outer-name

"""create_events() on a real database."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.models.events import CreateEventInput, CreateEventsInput
from app.models.onboarding import CreateUserInput
from app.models.speech_assessment import AssessmentCreateInput
from app.models.user_requests import DeleteUserDataInput


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    """A real SQLite database on the production schema, wired into app.db."""

    import app.db as db_module

    path = tmp_path / "events.db"
    schema = Path(db_module.__file__).with_name("schema.sql").read_text(encoding="utf-8")

    def _connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(db_module, "_get_connection", _connect)

    with closing(_connect()) as conn:
        conn.executescript(schema)

    return path


@pytest.fixture
def db(db_path):  # pylint: disable=unused-argument
    import app.db as db_module

    return db_module


def _add_user(db) -> str:
    guid = str(uuid4())
    db.create_user(CreateUserInput(
        guid=guid,
        consent_accepted=True,
        consent_timestamp=datetime.now(timezone.utc),
        finnish_self_assessment="A1",
    ))
    return guid


def _add_assessment(db, guid: str) -> int:
    return db.create_assessment(AssessmentCreateInput(
        guid=guid, task_id=1, audio_id=str(uuid4()), audio_path=f"/tmp/{uuid4()}.wav",
        transcript="hei", accuracy=2.0, fluency=2.0, proficiency=2.0, pronunciation=2.0,
        range_score=2.0,
    ))


def _event(index: int, assessment_ids: set[int] | None = None, **overrides) -> CreateEventInput:
    data = {
        "index": index,
        "event_id": uuid4(),
        "name": "app_opened",
        "properties_json": "{}",
        "occurred_at": "2026-10-02 12:00:00.000",
        "assessment_ids": assessment_ids or set(),
    }
    data.update(overrides)
    return CreateEventInput(**data)


def _batch(guid: str, events: list[CreateEventInput]) -> CreateEventsInput:
    return CreateEventsInput(guid=guid, session_id=uuid4(), app_version="2.1.0",
                             events=events)


def _count(db_path: Path, guid: str | None = None) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        if guid is None:
            return conn.execute("SELECT COUNT(*) FROM user_events").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM user_events WHERE guid = ?", (guid,)).fetchone()[0]


def test_create_events_returns_inserted_and_duplicate_counts(db, db_path):
    guid = _add_user(db)
    events = [_event(0), _event(1)]

    first = db.create_events(_batch(guid, events))
    second = db.create_events(_batch(guid, events + [_event(2)]))

    assert (first.inserted, first.duplicates) == (2, 0)
    assert (second.inserted, second.duplicates) == (1, 2)
    assert _count(db_path) == 3


def test_create_events_inserts_all_in_one_transaction(db, db_path):
    """One refused row rolls back the whole batch."""

    guid = _add_user(db)
    broken = _event(1, properties_json="[]")  # Fails the json_type CHECK.

    with pytest.raises(sqlite3.IntegrityError):
        db.create_events(_batch(guid, [_event(0), broken, _event(2)]))

    assert _count(db_path) == 0


def test_create_events_for_a_missing_user_raises_and_stores_nothing(db, db_path):
    with pytest.raises(sqlite3.IntegrityError):
        db.create_events(_batch(str(uuid4()), [_event(0)]))

    assert _count(db_path) == 0


def test_create_events_reports_foreign_references_by_index(db, db_path):
    guid = _add_user(db)
    mine = _add_assessment(db, guid)
    theirs = _add_assessment(db, _add_user(db))

    result = db.create_events(_batch(guid, [
        _event(0, {mine}), _event(3, {theirs}), _event(5, {mine, theirs}), _event(7),
    ]))

    assert result.inserted == 2
    assert result.foreign_reference_indices == [3, 5]
    assert _count(db_path, guid) == 2


def test_get_owned_assessment_ids_returns_only_the_users_ids(db, db_path):
    guid = _add_user(db)
    mine = {_add_assessment(db, guid), _add_assessment(db, guid)}
    theirs = _add_assessment(db, _add_user(db))

    with closing(sqlite3.connect(db_path)) as conn:
        owned = db._get_owned_assessment_ids(  # pylint: disable=protected-access
            conn, guid, mine | {theirs, 999_999})
        none = db._get_owned_assessment_ids(  # pylint: disable=protected-access
            conn, guid, set())

    assert owned == mine
    assert none == set()


def test_user_events_cascade_on_user_delete(db, db_path):
    guid = _add_user(db)
    other = _add_user(db)
    db.create_events(_batch(guid, [_event(0), _event(1)]))
    db.create_events(_batch(other, [_event(0)]))

    db.delete_user_data(DeleteUserDataInput(guid=guid))

    assert _count(db_path, guid) == 0
    assert _count(db_path, other) == 1
