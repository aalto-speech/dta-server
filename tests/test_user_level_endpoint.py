# pylint: disable=redefined-outer-name

import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Provide a FastAPI test client."""

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A real SQLite database on the production schema, wired into app.db."""

    from pathlib import Path

    import app.db as db_module

    path = tmp_path / "level.db"
    schema = (Path(db_module.__file__).with_name("schema.sql")).read_text(encoding="utf-8")

    def _connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(db_module, "_get_connection", _connect)

    with _connect() as conn:
        conn.executescript(schema)

    return db_module


def _add_user(db, guid: str, level: str = "A1"):
    from datetime import datetime, timezone

    from app.models.onboarding import CreateUserInput

    db.create_user(CreateUserInput(
        guid=guid,
        consent_accepted=True,
        consent_timestamp=datetime.now(timezone.utc),
        finnish_self_assessment=level,
    ))


def test_setting_a_level_leaves_the_self_assessment_untouched(db):
    """The onboarding self-report is the only record of what a learner believed at
    sign-up. Advance/Revert must never overwrite it."""

    from app.models.users import SetUserCEFRLevelInput

    guid = str(uuid4())
    _add_user(db, guid, "A1")

    assert db.set_user_cefr_level(
        SetUserCEFRLevelInput(guid=guid, cefr_level="B1")) is True

    with db.database() as conn:
        row = conn.execute(
            "SELECT cefr_level, current_cefr_level FROM users WHERE guid = ?",
            (guid,),
        ).fetchone()

    assert row[0] == "A1", "the self-assessment was overwritten"
    assert row[1] == "B1"


def test_level_is_null_until_the_learner_moves_and_reads_fall_back(db):
    """A user who never touches the buttons must behave exactly as before this feature."""

    guid = str(uuid4())
    _add_user(db, guid, "A2")

    with db.database() as conn:
        assert conn.execute(
            "SELECT current_cefr_level FROM users WHERE guid = ?", (guid,)
        ).fetchone()[0] is None
        # The COALESCE read is what every caller uses.
        assert db._get_user_cefr_level(conn, guid) == "A2"


def test_every_change_is_appended_to_the_history(db):
    """The gate is client-side only, so the server's record is the trustworthy one."""

    from app.models.users import SetUserCEFRLevelInput

    guid = str(uuid4())
    _add_user(db, guid, "A1")

    for level in ("A2", "B1", "A2"):
        db.set_user_cefr_level(SetUserCEFRLevelInput(guid=guid, cefr_level=level))

    with db.database() as conn:
        rows = conn.execute(
            "SELECT cefr_level, source FROM user_cefr_history WHERE guid = ? ORDER BY id",
            (guid,),
        ).fetchall()

    # Onboarding seeds the trail, so the starting level is there too.
    assert [r[0] for r in rows] == ["A1", "A2", "B1", "A2"]
    assert {r[1] for r in rows} == {"self_report"}


def test_repeating_the_same_target_is_idempotent(db):
    """The client sends a target, not a direction, so a retry must not move them twice."""

    from app.models.users import SetUserCEFRLevelInput

    guid = str(uuid4())
    _add_user(db, guid, "A1")

    for _ in range(3):
        db.set_user_cefr_level(SetUserCEFRLevelInput(guid=guid, cefr_level="A2"))

    with db.database() as conn:
        assert conn.execute(
            "SELECT current_cefr_level FROM users WHERE guid = ?", (guid,)
        ).fetchone()[0] == "A2"


def test_unknown_guid_reports_missing_rather_than_creating_anything(db):
    from app.models.users import SetUserCEFRLevelInput

    assert db.set_user_cefr_level(
        SetUserCEFRLevelInput(guid=str(uuid4()), cefr_level="B1")) is False


def test_cohort_ranking_follows_the_moved_level(db):
    """The profile screen promises a rank "among other B1 learners", so moving to B1
    must change who the learner is measured against."""

    from app.models.users import SetUserCEFRLevelInput

    moved, stayer = str(uuid4()), str(uuid4())
    _add_user(db, moved, "A1")
    _add_user(db, stayer, "B1")
    db.set_user_cefr_level(SetUserCEFRLevelInput(guid=moved, cefr_level="B1"))

    with db.database() as conn:
        cohort = conn.execute(
            "SELECT guid FROM users WHERE COALESCE(current_cefr_level, cefr_level) = ?",
            ("B1",),
        ).fetchall()

    assert {row[0] for row in cohort} == {moved, stayer}


def test_endpoint_rejects_a_level_outside_the_enum(client: TestClient):
    response = client.patch(
        "/users/level", data={"guid": str(uuid4()), "cefr_level": "C2"})

    assert response.status_code == 422


def test_endpoint_requires_both_fields(client: TestClient):
    assert client.patch(
        "/users/level", data={"guid": str(uuid4())}).status_code == 422
    assert client.patch(
        "/users/level", data={"cefr_level": "A2"}).status_code == 422


def test_endpoint_404s_for_a_user_that_does_not_exist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """validate_user_access runs first, so an unknown guid never reaches the write."""

    called = {"set": False}
    monkeypatch.setattr(
        "app.services.user_service.set_user_cefr_level",
        lambda _data: called.update(set=True) or True,
    )

    response = client.patch(
        "/users/level", data={"guid": str(uuid4()), "cefr_level": "A2"})

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "USER_NOT_FOUND"
    assert called["set"] is False


def test_endpoint_returns_the_stored_level(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.services.user_service.auth.validate_user_access", lambda _guid: None)
    monkeypatch.setattr(
        "app.services.user_service.set_user_cefr_level", lambda _data: True)

    guid = str(uuid4())
    response = client.patch(
        "/users/level", data={"guid": guid, "cefr_level": "A2"})

    assert response.status_code == 200
    assert response.json() == {"guid": guid, "cefr_level": "A2"}


def test_level_route_is_not_the_delete_route(client: TestClient):
    """`/users/level` is deliberately its own path: `/users` erases the account."""

    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/users/level" in paths
    assert "/users" in paths
