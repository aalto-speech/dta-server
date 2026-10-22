# pylint: disable=redefined-outer-name

"""create_events() on a real database."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

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
        "name": "task_panel_toggled",
        "properties_json": '{"active":true}',
        "occurred_at": "2026-10-02 12:00:00.000",
        "assessment_ids": assessment_ids or set(),
    }
    data.update(overrides)
    return CreateEventInput(**data)


def _batch(guid: str, events: list[CreateEventInput],
           session_id: UUID | None = None) -> CreateEventsInput:
    return CreateEventsInput(guid=guid, session_id=session_id or uuid4(),
                             app_version="2.1.0", events=events)


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


# --- user_event_views -------------------------------------------------------------------


def _toggle(index: int, name: str, at: str, active: bool, **properties) -> CreateEventInput:
    return _event(index, name=name, occurred_at=f"2026-10-02 12:{at}",
                  properties_json=json.dumps({"active": active, **properties}))


def _views(db_path: Path) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM user_event_views ORDER BY opened_at, name, dimension")]


def test_views_pair_each_activation_with_its_deactivation(db, db_path):
    """Nested and interleaved toggles pair by name, task and dimension, not by order."""

    guid = _add_user(db)
    db.create_events(_batch(guid, [
        _toggle(0, "feedback_panel_toggled", "00:00.000", True, task_id=2, assessment_id=None),
        _toggle(1, "dimension_toggled", "00:01.000", True, task_id=2, dimension="fluency"),
        _toggle(2, "dimension_toggled", "00:02.000", True, task_id=2, dimension="range"),
        _toggle(3, "dimension_toggled", "00:03.500", False, task_id=2, dimension="fluency"),
        _toggle(4, "dimension_toggled", "00:04.000", False, task_id=2, dimension="range"),
        _toggle(5, "feedback_panel_toggled", "01:30.250", False, task_id=2),
        _toggle(6, "asa_panel_toggled", "02:00.000", True, task_id=3),
        _toggle(7, "asa_panel_toggled", "02:10.000", False, task_id=3),
        _toggle(8, "asa_panel_toggled", "03:00.000", True, task_id=3),
        _toggle(9, "asa_panel_toggled", "03:05.000", False, task_id=3),
    ]))

    assert [(v["name"], v["task_id"], v["dimension"], v["duration_ms"])
            for v in _views(db_path)] == [
        ("feedback_panel_toggled", 2, None, 90_250),
        ("dimension_toggled", 2, "fluency", 2_500),
        ("dimension_toggled", 2, "range", 2_000),
        ("asa_panel_toggled", 3, None, 10_000),
        ("asa_panel_toggled", 3, None, 5_000),
    ]


def test_views_leave_an_unclosed_activation_without_a_duration(db, db_path):
    """A lost deactivation is NULL, never the gap to the next activation."""

    guid = _add_user(db)
    db.create_events(_batch(guid, [
        _toggle(0, "profile_panel_toggled", "00:00.000", True),
        _toggle(1, "profile_panel_toggled", "05:00.000", True),
        _toggle(2, "profile_panel_toggled", "05:01.000", False),
        _toggle(3, "info_popup_toggled", "06:00.000", True, task_id=1),
    ]))

    assert [(v["opened_at"], v["closed_at"], v["duration_ms"]) for v in _views(db_path)] == [
        ("2026-10-02 12:00:00.000", None, None),
        ("2026-10-02 12:05:00.000", "2026-10-02 12:05:01.000", 1_000),
        ("2026-10-02 12:06:00.000", None, None),
    ]


def test_views_do_not_pair_across_sessions_tasks_or_users(db, db_path):
    guid, other = _add_user(db), _add_user(db)
    session = uuid4()
    db.create_events(_batch(guid, [
        _toggle(0, "loading_panel_toggled", "00:00.000", True, task_id=1)], session))
    db.create_events(_batch(guid, [
        _toggle(0, "loading_panel_toggled", "00:01.000", False, task_id=1)]))
    db.create_events(_batch(guid, [
        _toggle(1, "loading_panel_toggled", "00:02.000", False, task_id=2)], session))
    db.create_events(_batch(other, [
        _toggle(0, "loading_panel_toggled", "00:03.000", False, task_id=1)], session))

    assert [v["duration_ms"] for v in _views(db_path)] == [None]


def test_views_ignore_clicks_and_scrolls(db, db_path):
    guid = _add_user(db)
    db.create_events(_batch(guid, [
        _event(0, name="button_clicked", properties_json='{"button_id":"asa"}'),
        _event(1, name="profile_panel_scrolled", properties_json='{"depth":0.5}'),
    ]))

    assert _views(db_path) == []
