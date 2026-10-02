"""Event vocabulary and property models, without HTTP.

The first two tests make CI fail for a new EventName without a model or examples.
"""

import typing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.models.events import (
    EVENT_PROPERTY_MODELS,
    MAX_EVENTS_PER_BATCH,
    EventBatchRequest,
    EventName,
    EventProperties,
    RejectionReason,
)
from app.validators.events import validate_events

# At least one valid and one invalid payload per EventName.
VALID_PROPERTIES: dict[EventName, dict] = {
    EventName.APP_OPENED: {},
    EventName.SCREEN_VIEWED: {"screen": "results_detail"},
    EventName.RESULT_VIEWED: {"assessment_id": 12},
}

INVALID_PROPERTIES: dict[EventName, list[dict]] = {
    EventName.APP_OPENED: [{"anything": 1}],
    EventName.SCREEN_VIEWED: [
        {},
        {"screen": ""},
        {"screen": "Results"},
        {"screen": "a" * 65},
        {"screen": 5},
        {"screen": "home", "title": "Home"},
    ],
    EventName.RESULT_VIEWED: [
        {},
        {"assessment_id": 0},
        {"assessment_id": -1},
        {"assessment_id": "12"},
        {"assessment_id": 1.5},
        {"assessment_id": False},
    ],
}


def test_every_event_name_has_a_property_model():
    assert set(EVENT_PROPERTY_MODELS) == set(EventName)


@pytest.mark.parametrize("name", list(EventName))
def test_every_event_name_has_test_examples(name: EventName):
    assert name in VALID_PROPERTIES, f"add a valid example for {name}"
    assert INVALID_PROPERTIES.get(name), f"add at least one invalid example for {name}"


@pytest.mark.parametrize("name", list(VALID_PROPERTIES))
def test_valid_example_properties_parse(name: EventName):
    EVENT_PROPERTY_MODELS[name].model_validate(VALID_PROPERTIES[name])


@pytest.mark.parametrize("name, properties", [
    (name, properties)
    for name, examples in INVALID_PROPERTIES.items()
    for properties in examples
])
def test_invalid_example_properties_fail(name: EventName, properties: dict):
    with pytest.raises(ValidationError):
        EVENT_PROPERTY_MODELS[name].model_validate(properties)


@pytest.mark.parametrize("model", list(EVENT_PROPERTY_MODELS.values()))
def test_all_property_models_forbid_extra_fields(model: type[BaseModel]):
    assert issubclass(model, EventProperties)
    assert model.model_config.get("extra") == "forbid"


def _is_str(annotation) -> bool:
    if annotation is str:
        return True
    return any(_is_str(arg) for arg in typing.get_args(annotation))


@pytest.mark.parametrize("model", list(EVENT_PROPERTY_MODELS.values()))
def test_property_models_have_no_unbounded_strings(model: type[BaseModel]):
    """Every string field must be bounded, to keep out free text."""

    for name, field in model.model_fields.items():
        if not _is_str(field.annotation):
            continue
        max_lengths = [getattr(meta, "max_length", None) for meta in field.metadata]
        assert any(length is not None for length in max_lengths), \
            f"{model.__name__}.{name} is a string without max_length"


@pytest.mark.parametrize("model", list(EVENT_PROPERTY_MODELS.values()))
def test_property_models_have_no_nested_free_form_values(model: type[BaseModel]):
    """No dicts, lists or Any: scalars only."""

    for name, field in model.model_fields.items():
        origin = typing.get_origin(field.annotation)
        assert field.annotation is not typing.Any, f"{model.__name__}.{name} is Any"
        assert origin not in (dict, list, set, tuple), \
            f"{model.__name__}.{name} is a free-form container"


def _batch(**overrides) -> dict:
    data = {
        "guid": str(uuid4()),
        "session_id": str(uuid4()),
        "app_version": "2.1.0+build.7",
        "events": [{}],
    }
    data.update(overrides)
    return data


def test_batch_request_accepts_a_valid_envelope():
    EventBatchRequest.model_validate(_batch())


def test_batch_request_forbids_extra_fields():
    with pytest.raises(ValidationError):
        EventBatchRequest.model_validate(_batch(ip_address="10.0.0.1"))


@pytest.mark.parametrize("count, ok", [
    (0, False), (1, True), (MAX_EVENTS_PER_BATCH, True), (MAX_EVENTS_PER_BATCH + 1, False),
])
def test_batch_request_bounds_the_event_count(count: int, ok: bool):
    data = _batch(events=[{}] * count)

    if ok:
        EventBatchRequest.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            EventBatchRequest.model_validate(data)


# --- validate_events, directly ----------------------------------------------------------

NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def _raw(name: str = "app_opened", **overrides) -> dict:
    event = {"event_id": str(uuid4()), "name": name, "occurred_at": NOW.isoformat()}
    event.update(overrides)
    return event


def test_validate_events_normalises_to_utc_and_collects_assessment_refs():
    helsinki = timezone(timedelta(hours=3))
    valid, rejected = validate_events([
        _raw("result_viewed", properties={"assessment_id": 4},
             occurred_at=NOW.astimezone(helsinki).isoformat()),
    ], now=NOW)

    assert rejected == []
    assert len(valid) == 1
    event = valid[0]
    assert event.occurred_at == NOW
    assert event.occurred_at.tzinfo == timezone.utc
    assert event.assessment_ids == {4}


def test_validate_events_keeps_request_indices():
    valid, rejected = validate_events([_raw("nope"), _raw(), _raw("nope")], now=NOW)

    assert [event.index for event in valid] == [1]
    assert [(item.index, item.reason) for item in rejected] == [
        (0, RejectionReason.UNKNOWN_EVENT), (2, RejectionReason.UNKNOWN_EVENT)]


def test_validate_events_without_assessment_reference_has_no_refs():
    valid, _ = validate_events([_raw("screen_viewed", properties={"screen": "home"})], now=NOW)

    assert valid[0].assessment_ids == set()
