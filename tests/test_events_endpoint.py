# pylint: disable=redefined-outer-name

"""POST /events. Batch-level faults store nothing. Event-level faults reject only that event."""

import gzip
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.events import EVENT_PROPERTY_MODELS, MAX_EVENTS_PER_BATCH, EventName
from app.models.speech_assessment import AssessmentCreateInput
from app.services.event_service import _utcnow as real_utcnow
from app.utils.gzip_request import MAX_DECOMPRESSED_BODY_BYTES

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Path:
    """A real SQLite database on the production schema, wired into app.db."""

    import app.db as db_module

    path = tmp_path / "events.db"
    schema = Path(db_module.__file__).with_name(
        "schema.sql").read_text(encoding="utf-8")

    def _connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(db_module, "_get_connection", _connect)

    with closing(_connect()) as conn:
        conn.executescript(schema)

    return path


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch):
    """Freeze the server clock."""

    monkeypatch.setattr("app.services.event_service._utcnow", lambda: NOW)


@pytest.fixture
def client(db_path):  # pylint: disable=unused-argument
    """FastAPI test client on the temporary database."""

    with TestClient(app) as test_client:
        yield test_client


def _onboard(client: TestClient, consent: bool = True) -> str:
    guid = str(uuid4())
    response = client.post("/onboarding", data={
        "guid": guid,
        "consent_accepted": "true" if consent else "false",
        "consent_timestamp": "2026-01-01T00:00:00Z",
        "finnish_self_assessment": "A1",
    })
    assert response.status_code == 201
    return guid


@pytest.fixture
def guid(client) -> str:
    return _onboard(client)


def _add_assessment(guid: str) -> int:
    from app.db import create_assessment

    return create_assessment(AssessmentCreateInput(
        guid=guid,
        task_id=1,
        audio_id=str(uuid4()),
        audio_path=f"/tmp/{uuid4()}.wav",
        transcript="hei",
        accuracy=2.0,
        fluency=2.0,
        proficiency=2.0,
        pronunciation=2.0,
        range_score=2.0,
    ))


def _event(name: str = "task_panel_toggled", **overrides) -> dict:
    event = {
        "event_id": str(uuid4()),
        "name": name,
        "occurred_at": NOW.isoformat(),
        "properties": {"active": True},
    }
    event.update(overrides)
    return event


def _valid_event_batch(user_guid: str, **overrides) -> dict:
    data = {
        "guid": user_guid,
        "session_id": str(uuid4()),
        "app_version": "2.1.0",
        "events": [
            _event("task_panel_toggled"),
            _event("button_clicked", properties={"button_id": "profile"}),
        ],
    }
    data.update(overrides)
    return data


def _rows(db_path: Path) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM user_events ORDER BY id")]


# --- happy path -------------------------------------------------------------------------


def test_valid_batch_returns_202_with_counts(client: TestClient, guid: str, db_path: Path):
    response = client.post("/events", json=_valid_event_batch(guid))

    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": []}
    assert len(_rows(db_path)) == 2


def test_stored_row_holds_the_envelope_and_canonical_properties(
    client: TestClient, guid: str, db_path: Path,
):
    """Properties are stored from the validated model, not the raw request."""

    batch = _valid_event_batch(guid, events=[
        _event("asa_panel_toggled", properties={"task_id": 3, "active": True}),
    ])

    client.post("/events", json=batch)

    [row] = _rows(db_path)
    assert row["guid"] == guid
    assert row["event_id"] == batch["events"][0]["event_id"]
    assert row["session_id"] == batch["session_id"]
    assert row["name"] == "asa_panel_toggled"
    assert row["properties"] == '{"active":true,"task_id":3}'
    assert row["app_version"] == "2.1.0"
    assert row["received_at"]


def test_unset_optional_properties_are_left_out_of_the_row(
    client: TestClient, guid: str, db_path: Path,
):
    client.post("/events", json=_valid_event_batch(guid, events=[
        _event("button_clicked", properties={"button_id": "asa"}),
        _event("feedback_panel_toggled", properties={"active": False, "task_id": 2}),
    ]))

    assert [row["properties"] for row in _rows(db_path)] == [
        '{"button_id":"asa"}', '{"active":false,"task_id":2}']


def test_event_without_properties_is_rejected(client: TestClient, guid: str):
    """Every event has a required property, so a missing object is a fault."""

    event = _event()
    del event["properties"]

    response = client.post(
        "/events", json=_valid_event_batch(guid, events=[event]))

    assert response.json()["rejected"][0]["reason"] == "INVALID_PROPERTIES"


def test_occurred_at_is_stored_as_utc(client: TestClient, guid: str, db_path: Path):
    """A +03:00 timestamp is stored in UTC, in received_at's format."""

    helsinki = timezone(timedelta(hours=3))
    local = NOW.astimezone(helsinki) - timedelta(minutes=1, milliseconds=250)

    client.post("/events", json=_valid_event_batch(guid, events=[
        _event(occurred_at=local.isoformat()),
    ]))

    assert _rows(db_path)[0]["occurred_at"] == "2026-10-02 11:58:59.750"


# --- batch size -------------------------------------------------------------------------


def test_more_than_100_events_returns_422(client: TestClient, guid: str, db_path: Path):
    events = [_event() for _ in range(MAX_EVENTS_PER_BATCH + 1)]

    response = client.post(
        "/events", json=_valid_event_batch(guid, events=events))

    assert response.status_code == 422
    assert _rows(db_path) == []


def test_exactly_100_events_are_accepted(client: TestClient, guid: str):
    events = [_event() for _ in range(MAX_EVENTS_PER_BATCH)]

    response = client.post(
        "/events", json=_valid_event_batch(guid, events=events))

    assert response.json()["accepted"] == MAX_EVENTS_PER_BATCH


def test_empty_events_list_returns_422(client: TestClient, guid: str):
    response = client.post("/events", json=_valid_event_batch(guid, events=[]))

    assert response.status_code == 422


# --- envelope ---------------------------------------------------------------------------


@pytest.mark.parametrize("override", [
    {"guid": "not-a-guid"},
    {"session_id": "not-a-uuid"},
    {"app_version": "1" * 33},
    {"app_version": ""},
    {"app_version": "2.1.0; DROP TABLE users"},
    {"device_model": "Pixel 8"},
    {"events": [42]},
    {"events": "task_panel_toggled"},
], ids=["guid", "session_id", "app_version_long", "app_version_empty",
        "app_version_chars", "unknown_key", "event_not_object", "events_not_list"])
def test_invalid_envelope_returns_422_and_stores_nothing(
    client: TestClient, guid: str, db_path: Path, override: dict,
):
    response = client.post(
        "/events", json=_valid_event_batch(guid, **override))

    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "VALIDATION_ERROR"
    assert _rows(db_path) == []


@pytest.mark.parametrize("missing", ["guid", "session_id", "app_version", "events"])
def test_missing_envelope_field_returns_422(client: TestClient, guid: str, missing: str):
    batch = _valid_event_batch(guid)
    del batch[missing]

    response = client.post("/events", json=batch)

    assert response.status_code == 422


def test_body_that_is_not_json_returns_422(client: TestClient, db_path: Path):
    response = client.post(
        "/events", content=b"{not json", headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert _rows(db_path) == []


def test_form_encoded_body_returns_422(client: TestClient, guid: str):
    response = client.post("/events", data={"guid": guid})

    assert response.status_code == 422


# --- access -----------------------------------------------------------------------------


def test_unknown_guid_returns_404(client: TestClient, db_path: Path):
    response = client.post("/events", json=_valid_event_batch(str(uuid4())))

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "USER_NOT_FOUND"
    assert _rows(db_path) == []


def test_user_without_consent_returns_403(client: TestClient, db_path: Path):
    guid = _onboard(client, consent=False)

    response = client.post("/events", json=_valid_event_batch(guid))

    assert response.status_code == 403
    assert response.json()["detail"]["type"] == "USER_CONSENT_MISSING"
    assert _rows(db_path) == []


# --- per event: partial accept ----------------------------------------------------------


@pytest.mark.parametrize("bad_event, reason", [
    (_event("app_closed_forever"), "UNKNOWN_EVENT"),
    (_event("TASK_PANEL_TOGGLED"), "UNKNOWN_EVENT"),
    (_event(properties={"active": True, "note": "I hate this app"}), "INVALID_PROPERTIES"),
    (_event(properties={}), "INVALID_PROPERTIES"),
    (_event("button_clicked", properties={"button_id": "settings"}), "INVALID_PROPERTIES"),
    (_event("dimension_toggled", properties={
     "active": True, "task_id": 1, "dimension": "Fluency"}), "INVALID_PROPERTIES"),
    (_event("asa_panel_toggled", properties={
     "active": True, "task_id": 6}), "INVALID_PROPERTIES"),
    (_event("asa_panel_toggled", properties={
     "active": True, "task_id": "1"}), "INVALID_PROPERTIES"),
    (_event("asa_panel_toggled", properties={
     "active": True, "task_id": True}), "INVALID_PROPERTIES"),
    (_event(properties={"active": 1}), "INVALID_PROPERTIES"),
    (_event("button_clicked", properties={"button_id": "record"}), "INVALID_PROPERTIES"),
    (_event("button_clicked", properties={
     "button_id": "profile", "task_id": 1}), "INVALID_PROPERTIES"),
    (_event("profile_panel_scrolled", properties={"depth": 1.5}), "INVALID_PROPERTIES"),
    (_event(occurred_at=(NOW - timedelta(days=31)).isoformat()),
     "TIMESTAMP_OUT_OF_RANGE"),
    (_event(occurred_at=(NOW + timedelta(minutes=6)).isoformat()),
     "TIMESTAMP_OUT_OF_RANGE"),
    (_event(occurred_at="2026-10-02T12:00:00"), "INVALID_EVENT"),
    (_event(occurred_at="yesterday"), "INVALID_EVENT"),
    (_event(extra_key=1), "INVALID_EVENT"),
    (_event(properties="active=true"), "INVALID_EVENT"),
    (_event("x" * 65), "INVALID_EVENT"),
], ids=["unknown_name", "name_case", "extra_property", "missing_property", "unknown_button",
        "unknown_dimension", "number_out_of_range", "number_as_string", "bool_as_int",
        "int_as_bool", "button_missing_task", "button_unexpected_task", "depth_out_of_range",
        "too_old", "too_far_ahead", "naive_datetime", "unparseable_datetime", "event_extra_key",
        "properties_not_object", "name_too_long"])
def test_invalid_event_is_rejected_and_others_stored(
    client: TestClient, guid: str, db_path: Path, bad_event: dict, reason: str,
):
    good = _event()
    batch = _valid_event_batch(
        guid, events=[good, bad_event, _event()])

    response = client.post("/events", json=batch)

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == [
        {"index": 1, "event_id": bad_event["event_id"], "reason": reason}]
    stored = {row["event_id"] for row in _rows(db_path)}
    assert good["event_id"] in stored
    assert bad_event["event_id"] not in stored


@pytest.mark.parametrize("event_id", ["not-a-uuid", None, 7])
def test_event_with_invalid_event_id_is_rejected_without_an_id(
    client: TestClient, guid: str, event_id,
):
    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event(event_id=event_id), _event(),
    ]))

    assert response.json()["rejected"] == [
        {"index": 0, "event_id": None, "reason": "INVALID_EVENT"}]
    assert response.json()["accepted"] == 1


def test_timestamp_boundaries_are_accepted(client: TestClient, guid: str):
    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event(occurred_at=(
            NOW - timedelta(days=30)).isoformat()),
        _event(occurred_at=(
            NOW + timedelta(minutes=5)).isoformat()),
    ]))

    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": []}


def test_batch_where_every_event_is_rejected_still_returns_202(
    client: TestClient, guid: str, db_path: Path,
):
    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event("nope"), _event("nope"),
    ]))

    assert response.status_code == 202
    assert response.json()["accepted"] == 0
    assert [item["index"] for item in response.json()["rejected"]] == [0, 1]
    assert _rows(db_path) == []


# --- ownership --------------------------------------------------------------------------


def test_assessment_id_of_another_user_is_rejected(
    client: TestClient, guid: str, db_path: Path,
):
    someone_else = _onboard(client)
    their_assessment = _add_assessment(someone_else)
    event = _event("feedback_panel_toggled", properties={
                   "active": True, "task_id": 1, "assessment_id": their_assessment})

    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event(), event,
    ]))

    assert response.json()["rejected"] == [
        {"index": 1, "event_id": event["event_id"], "reason": "FOREIGN_REFERENCE"}]
    assert response.json()["accepted"] == 1
    assert event["event_id"] not in {row["event_id"] for row in _rows(db_path)}


def test_own_assessment_id_is_accepted(client: TestClient, guid: str, db_path: Path):
    mine = _add_assessment(guid)

    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event("feedback_panel_toggled",
               properties={"active": True, "task_id": 1, "assessment_id": mine}),
    ]))

    assert response.json() == {"accepted": 1, "duplicates": 0, "rejected": []}
    assert _rows(db_path)[0]["properties"] == \
        f'{{"active":true,"assessment_id":{mine},"task_id":1}}'


def test_nonexistent_assessment_id_is_rejected(client: TestClient, guid: str):
    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event("feedback_panel_toggled",
               properties={"active": True, "task_id": 1, "assessment_id": 999_999}),
    ]))

    assert response.json()["rejected"][0]["reason"] == "FOREIGN_REFERENCE"


def test_rejections_from_both_stages_come_back_in_request_order(
    client: TestClient, guid: str,
):
    response = client.post("/events", json=_valid_event_batch(guid, events=[
        _event("feedback_panel_toggled",
               properties={"active": True, "task_id": 1, "assessment_id": 999_999}),
        _event("nope"),
        _event(),
    ]))

    assert [(item["index"], item["reason"]) for item in response.json()["rejected"]] == [
        (0, "FOREIGN_REFERENCE"), (1, "UNKNOWN_EVENT")]


# --- idempotency ------------------------------------------------------------------------


def test_resent_batch_counts_duplicates_and_adds_no_rows(
    client: TestClient, guid: str, db_path: Path,
):
    batch = _valid_event_batch(guid)
    client.post("/events", json=batch)

    response = client.post("/events", json=batch)

    assert response.json() == {"accepted": 0, "duplicates": 2, "rejected": []}
    assert len(_rows(db_path)) == 2


def test_same_event_id_for_two_users_is_stored_for_both(
    client: TestClient, guid: str, db_path: Path,
):
    other = _onboard(client)
    shared = _event()

    first = client.post(
        "/events", json=_valid_event_batch(guid, events=[shared]))
    second = client.post(
        "/events", json=_valid_event_batch(other, events=[shared]))

    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 1
    assert {row["guid"] for row in _rows(db_path)} == {guid, other}


def test_duplicate_event_id_within_one_batch_is_stored_once(
    client: TestClient, guid: str, db_path: Path,
):
    event = _event()

    response = client.post(
        "/events", json=_valid_event_batch(guid, events=[event, event]))

    assert response.json() == {"accepted": 1, "duplicates": 1, "rejected": []}
    assert len(_rows(db_path)) == 1


# --- atomicity --------------------------------------------------------------------------


def test_user_deleted_between_check_and_insert_returns_409_and_stores_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_path: Path,
):
    """The FK, not the access check, prevents orphaned events."""

    monkeypatch.setattr("app.services.event_service.auth.validate_user_access",
                        lambda _guid: None)

    response = client.post("/events", json=_valid_event_batch(str(uuid4())))

    assert response.status_code == 409
    assert response.json()["detail"]["type"] == "DATABASE_CONSTRAINT_ERROR"
    assert _rows(db_path) == []


# --- privacy ----------------------------------------------------------------------------


def test_delete_users_removes_user_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, guid: str, db_path: Path,
):
    """Uses the real DELETE /users, so the cascade is tested."""

    survivor = _onboard(client)
    client.post("/events", json=_valid_event_batch(guid))
    client.post("/events", json=_valid_event_batch(survivor))
    monkeypatch.setattr("app.services.admin_service.auth.validate_delete_access",
                        lambda _key: None)

    response = client.request("DELETE", "/users", headers={"X-Delete-Key": "k"},
                              data={"guid": guid})

    assert response.status_code == 204
    assert {row["guid"] for row in _rows(db_path)} == {survivor}


def test_logs_contain_counts_but_no_guid_or_property_values(
    client: TestClient, guid: str, caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    batch = _valid_event_batch(guid, events=[
        _event("dimension_toggled",
               properties={"active": True, "task_id": 1, "dimension": "pronunciation"}),
        _event("nope"),
    ])
    caplog.clear()

    client.post("/events", json=batch)

    text = caplog.text
    assert "Stored events: accepted=1 duplicates=0 rejected=1" in text
    assert "UNKNOWN_EVENT=1" in text
    assert guid not in text
    assert "pronunciation" not in text
    assert batch["session_id"] not in text


def test_422_logs_contain_no_payload(client: TestClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)

    client.post("/events", json={"guid": "secret_guid_marker", "events": []})

    assert "Validation error" in caplog.text
    assert "secret_guid_marker" not in caplog.text


# --- gzip -------------------------------------------------------------------------------


def _post_raw(client: TestClient, body: bytes, encoding: str | None = None):
    headers = {"content-type": "application/json"}
    if encoding is not None:
        headers["content-encoding"] = encoding
    return client.post("/events", content=body, headers=headers)


@pytest.mark.parametrize("encoding", ["gzip", "x-gzip", "GZIP"])
def test_gzip_batch_is_accepted(client: TestClient, guid: str, db_path: Path, encoding: str):
    body = json.dumps(_valid_event_batch(guid)).encode()

    response = _post_raw(client, gzip.compress(body), encoding)

    assert response.status_code == 202
    assert response.json() == {"accepted": 2, "duplicates": 0, "rejected": []}
    assert len(_rows(db_path)) == 2


@pytest.mark.parametrize("encoding", [None, "identity"])
def test_uncompressed_batch_is_still_accepted(client: TestClient, guid: str, encoding):
    response = _post_raw(client, json.dumps(_valid_event_batch(guid)).encode(), encoding)

    assert response.json()["accepted"] == 2


def test_gzip_and_plain_resends_of_one_batch_are_duplicates(
    client: TestClient, guid: str, db_path: Path,
):
    body = json.dumps(_valid_event_batch(guid)).encode()

    _post_raw(client, gzip.compress(body), "gzip")
    response = _post_raw(client, body)

    assert response.json() == {"accepted": 0, "duplicates": 2, "rejected": []}
    assert len(_rows(db_path)) == 2


def test_gzip_body_is_validated_like_any_other(client: TestClient, guid: str, db_path: Path):
    body = json.dumps(_valid_event_batch(guid, platform="android")).encode()

    response = _post_raw(client, gzip.compress(body), "gzip")

    assert response.status_code == 422
    assert _rows(db_path) == []


@pytest.mark.parametrize("body", [
    b"not gzip",
    gzip.compress(b'{"guid": "x"}')[:-6],
], ids=["garbage", "truncated"])
def test_invalid_gzip_returns_400(client: TestClient, db_path: Path, body: bytes):
    response = _post_raw(client, body, "gzip")

    assert response.status_code == 400
    assert response.json()["detail"]["type"] == "BAD_REQUEST"
    assert _rows(db_path) == []


def test_unsupported_encoding_returns_415(client: TestClient, guid: str, db_path: Path):
    response = _post_raw(client, json.dumps(_valid_event_batch(guid)).encode(), "br")

    assert response.status_code == 415
    assert response.json()["detail"]["type"] == "UNSUPPORTED_MEDIA_TYPE"
    assert _rows(db_path) == []


def test_gzip_body_inflating_past_the_limit_returns_413(client: TestClient, db_path: Path):
    """About 10 KB on the wire, over 10 MB once inflated."""

    bomb = gzip.compress(b" " * (MAX_DECOMPRESSED_BODY_BYTES + 1))

    response = _post_raw(client, bomb, "gzip")

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "type": "PAYLOAD_TOO_LARGE",
        "message": "Decompressed request body exceeds the 10 MB limit.",
        "max_size_bytes": MAX_DECOMPRESSED_BODY_BYTES,
    }
    assert _rows(db_path) == []


def test_gzip_body_at_exactly_the_limit_is_parsed(client: TestClient, guid: str):
    body = json.dumps(_valid_event_batch(guid)).encode()
    body += b" " * (MAX_DECOMPRESSED_BODY_BYTES - len(body))

    response = _post_raw(client, gzip.compress(body), "gzip")

    assert response.status_code == 202


def test_gzip_is_only_handled_on_events(client: TestClient):
    """Other routes ignore the encoding header."""

    response = client.post("/feedback", content=gzip.compress(b"guid=x"),
                           headers={"content-type": "application/x-www-form-urlencoded",
                                    "content-encoding": "gzip"})

    assert response.status_code == 422


# --- OpenAPI ----------------------------------------------------------------------------


def test_events_route_documents_error_responses():
    operation = app.openapi()["paths"]["/events"]["post"]

    for status in ("400", "403", "404", "409", "413", "415"):
        schema = operation["responses"][status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}
    assert "202" in operation["responses"]


def test_events_route_publishes_each_event_and_its_properties():
    """Generated clients see one typed event per name, discriminated by `name`."""

    schemas = app.openapi()["components"]["schemas"]
    items = schemas["EventBatchRequest"]["properties"]["events"]["items"]
    mapping = items["discriminator"]["mapping"]

    assert items["discriminator"]["propertyName"] == "name"
    assert set(mapping) == {name.value for name in EventName}
    assert sorted(ref["$ref"] for ref in items["oneOf"]) == sorted(mapping.values())
    for name, ref in mapping.items():
        event = schemas[ref.rsplit("/", 1)[-1]]
        assert event["properties"]["name"]["const"] == name
        assert set(event["required"]) == {"event_id", "name", "occurred_at", "properties"}
        properties_ref = event["properties"]["properties"]["$ref"].rsplit("/", 1)[-1]
        assert properties_ref == EVENT_PROPERTY_MODELS[EventName(name)].__name__


def test_event_that_is_not_an_object_rejects_the_batch(client: TestClient, guid: str):
    """The published schema is documentation only; the batch accepts any object."""

    response = client.post("/events", json=_valid_event_batch(
        guid, events=[_event(), "task_panel_toggled"]))

    assert response.status_code == 422


def test_real_server_clock_is_aware_utc():
    """The unfrozen clock production uses."""

    now = real_utcnow()

    assert now.tzinfo == timezone.utc
    assert abs(datetime.now(timezone.utc) - now) < timedelta(seconds=5)
