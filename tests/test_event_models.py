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
    Button,
    Dimension,
    EventProperties,
    Language,
    RejectionReason,
)
from app.models.speech_assessment import SpeechAssessmentScores
from app.validators.events import validate_events

# At least one valid and one invalid payload per EventName.
VALID_PROPERTIES: dict[EventName, dict] = {
    EventName.ASA_SESSION_STARTED: {"language": "fi"},
    EventName.ASA_SESSION_ENDED: {"reason": "timeout"},
    EventName.TASK_SELECTION_PANEL_TOGGLED: {"active": True},
    EventName.PROFILE_PANEL_TOGGLED: {"active": False},
    EventName.TASK_PANEL_TOGGLED: {"active": True, "task_id": 1},
    EventName.LOADING_MODAL_TOGGLED: {"active": False, "task_id": 6},
    EventName.FEEDBACK_PANEL_TOGGLED: {"active": True, "task_id": 2, "assessment_id": 12},
    EventName.INFO_MODAL_TOGGLED: {"active": True, "task_id": 3},
    EventName.DIMENSION_MODAL_TOGGLED: {"active": True, "task_id": 4, "dimension": "fluency"},
    EventName.PROFILE_PANEL_SCROLLED: {"depth": 0.25},
    EventName.FEEDBACK_PANEL_SCROLLED: {"depth": 1, "task_id": 2},
    EventName.MIC_BUTTON_CLICKED: {"button_id": "record", "task_id": 3},
    EventName.LANGUAGE_CHANGED: {"from_language": "fi", "to_language": "fa"},
}

_TOGGLE_FAULTS = [{}, {"active": 1}, {"active": "true"}, {"active": None}]
_TASK_FAULTS = [{"task_id": 0}, {"task_id": -1}, {"task_id": "1"}, {"task_id": True},
                {"task_id": 1.5}, {"task_id": None}]
_FEEDBACK_PANEL = {"active": True, "task_id": 1, "assessment_id": 4}
_DIMENSION_MODAL = {"active": True, "task_id": 1, "dimension": "range"}


def _task_scoped_faults(valid: dict) -> list[dict]:
    """Toggle and task faults applied to a valid task-scoped toggle, plus a missing task."""

    return (
        [{**valid, **fault} for fault in _TOGGLE_FAULTS[1:] + _TASK_FAULTS]
        + [{k: v for k, v in valid.items() if k != "task_id"},
           {**valid, "title": "Results"}]
    )


INVALID_PROPERTIES: dict[EventName, list[dict]] = {
    EventName.ASA_SESSION_STARTED: [
        {"reason": "quit"}, {"anything": 1}, {"language": "FI"}, {"language": "de"},
        {"language": ""}, {"language": 1}, {"language": "fi", "anything": 1},
    ],
    EventName.ASA_SESSION_ENDED: [
        {"reason": "Quit"}, {"reason": ""}, {"reason": "crashed"}, {"reason": 1},
        {"reason": None}, {"reason": "quit", "screen": "profile"},
    ],
    EventName.TASK_SELECTION_PANEL_TOGGLED: _TOGGLE_FAULTS + [{"active": True, "task_id": 1}],
    EventName.PROFILE_PANEL_TOGGLED: _TOGGLE_FAULTS + [{"active": True, "anything": 1}],
    EventName.TASK_PANEL_TOGGLED: _task_scoped_faults({"active": True, "task_id": 1}),
    EventName.LOADING_MODAL_TOGGLED: _task_scoped_faults({"active": True, "task_id": 1}),
    EventName.FEEDBACK_PANEL_TOGGLED: _task_scoped_faults(_FEEDBACK_PANEL) + [
        {"active": True, "task_id": 1},
        {**_FEEDBACK_PANEL, "assessment_id": 0},
        {**_FEEDBACK_PANEL, "assessment_id": "12"},
        {**_FEEDBACK_PANEL, "assessment_id": False},
    ],
    EventName.INFO_MODAL_TOGGLED: _task_scoped_faults({"active": True, "task_id": 1}),
    EventName.DIMENSION_MODAL_TOGGLED: _task_scoped_faults(_DIMENSION_MODAL) + [
        {"active": True, "task_id": 1},
        {**_DIMENSION_MODAL, "dimension": "Fluency"},
        {**_DIMENSION_MODAL, "dimension": "range_score"},
        {**_DIMENSION_MODAL, "dimension": 1},
    ],
    EventName.PROFILE_PANEL_SCROLLED: [
        {}, {"depth": -0.01}, {"depth": 1.01}, {"depth": "0.5"}, {"depth": True},
        {"depth": None}, {"depth": 0.5, "task_id": 1}, {"depth": 0.5, "direction": "down"},
    ],
    EventName.FEEDBACK_PANEL_SCROLLED: [
        {"depth": 0.5}, {"task_id": 1}, {"depth": 2, "task_id": 1},
        {"depth": 0.5, "task_id": 0}, {"depth": 0.5, "task_id": "1"},
    ],
    EventName.MIC_BUTTON_CLICKED: [
        {},
        {"task_id": 1},
        {"button_id": "", "task_id": 1},
        {"button_id": "settings", "task_id": 1},
        {"button_id": "Record", "task_id": 1},
        {"button_id": 1, "task_id": 1},
        {"button_id": "record", "task_id": 0},
        {"button_id": "record", "task_id": "1"},
        {"button_id": "record", "task_id": 1, "label": "Record"},
    ],
    EventName.LANGUAGE_CHANGED: [
        {},
        {"from_language": "en"},
        {"to_language": "fi"},
        {"from_language": "EN", "to_language": "fi"},
        {"from_language": "en", "to_language": "de"},
        {"from_language": "en", "to_language": "fi-FI"},
        {"from_language": "en", "to_language": ""},
        {"from_language": "en", "to_language": 1},
        {"from_language": None, "to_language": "fi"},
        {"from_language": "en", "to_language": "fi", "screen": "advanced"},
    ],
}


@pytest.mark.parametrize("button", list(Button))
def test_every_mic_button_needs_a_task(button: Button):
    """Record, stop and replay all happen on a task, so each needs its task_id."""

    model = EVENT_PROPERTY_MODELS[EventName.MIC_BUTTON_CLICKED]

    model.model_validate({"button_id": button.value, "task_id": 2})
    with pytest.raises(ValidationError):
        model.model_validate({"button_id": button.value})


def test_asa_session_started_language_is_optional():
    """A client that does not send the language still has its session start recorded."""

    model = EVENT_PROPERTY_MODELS[EventName.ASA_SESSION_STARTED]

    assert model.model_validate({}).model_dump(mode="json") == {"language": None}
    assert model.model_validate({"language": "fa"}).model_dump(mode="json") == {
        "language": "fa"}


def test_asa_session_ended_defaults_to_a_quit():
    model = EVENT_PROPERTY_MODELS[EventName.ASA_SESSION_ENDED]

    assert model.model_validate({}).model_dump() == {"reason": "quit"}


def test_dimensions_match_the_score_keys_of_speech_assess():
    assert {d.value for d in Dimension} == set(SpeechAssessmentScores.model_fields)


def test_languages_match_the_apps_locale_codes():
    """The client's Unity locale codes. A new app language must be added on both sides."""

    assert {language.value for language in Language} == {
        "bn", "en", "et", "fa", "fi", "ru", "sv", "uk", "vi", "zh"}


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
        "asa_session_id": str(uuid4()),
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


def _raw(name: str = "task_selection_panel_toggled", **overrides) -> dict:
    event = {"event_id": str(uuid4()), "name": name, "occurred_at": NOW.isoformat(),
             "properties": {"active": True}}
    event.update(overrides)
    return event


def test_validate_events_normalises_to_utc_and_collects_assessment_refs():
    helsinki = timezone(timedelta(hours=3))
    valid, rejected = validate_events([
        _raw("feedback_panel_toggled",
             properties={"active": True, "task_id": 1, "assessment_id": 4},
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
    valid, _ = validate_events([
        _raw("task_panel_toggled", properties={"active": True, "task_id": 1}),
    ], now=NOW)

    assert valid[0].assessment_ids == set()
