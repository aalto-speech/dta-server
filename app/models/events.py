from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Union, cast
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainValidator,
    StrictBool,
    StrictInt,
    create_model,
)

MAX_EVENTS_PER_BATCH = 100

# Outside this window: a device clock fault or a stale buffer.
MAX_EVENT_AGE = timedelta(days=30)
MAX_EVENT_CLOCK_SKEW = timedelta(minutes=5)

EVENT_NAME_MAX_LENGTH = 64
QUIT_REASON_MAX_LENGTH = 16


class EventProperties(BaseModel):
    """Base model for event properties.

    Every key is declared and bounded, so no free text can be stored. Tests enforce
    `extra="forbid"` and `max_length` on string fields.
    """

    model_config = ConfigDict(extra="forbid")


# StrictInt rejects "5" and booleans.
TaskId = Annotated[StrictInt, Field(ge=1)]

# Fraction of the panel scrolled past: 0 is the top, 1 the bottom. Unity's
# verticalNormalizedPosition runs the other way, so the client sends 1 minus it.
ScrollDepth = Annotated[float, Field(
    ge=0, le=1, strict=True, allow_inf_nan=False)]


class Button(StrEnum):
    """Accepted `button_id` values for `mic_button_clicked`.

    These are currently used only for the record button, but the enum could be extended for other buttons.
    """

    RECORD = "record"
    REPLAY = "replay"
    STOP = "stop"


class Dimension(StrEnum):
    """Accepted `dimension` values, the score keys of POST /speech/assess."""

    PROFICIENCY = "proficiency"
    PRONUNCIATION = "pronunciation"
    RANGE = "range"
    ACCURACY = "accuracy"
    FLUENCY = "fluency"


class QuitReason(StrEnum):
    """Accepted `reason` values for `asa_session_ended`."""

    QUIT = "quit"
    TIMEOUT = "timeout"
    ERROR = "error"


class ASASessionStartedProperties(EventProperties):
    """Properties for `asa_session_started`, the first event of a session. None."""


class ASASessionEndedProperties(EventProperties):
    """Properties for `asa_session_ended`, the last event of a session. None.

    Sent on quitting from any screen, so its `occurred_at` is the quit time.
    """

    reason: QuitReason = Field(default=QuitReason.QUIT)


class PanelToggledProperties(EventProperties):
    """Base model for `*_toggled` events.

    Attributes:
        active: Whether the panel is active after the toggle.
    """

    active: StrictBool


class TaskSelectionPanelToggledProperties(PanelToggledProperties):
    """Properties for `task_selection_panel_toggled`. The task list shows no task."""


class ProfilePanelToggledProperties(PanelToggledProperties):
    """Properties for `profile_panel_toggled`."""


class TaskScopedPanelToggledProperties(PanelToggledProperties):
    """Base model for toggles of panels and modals that show a task.

    Attributes:
        task_id: The task on screen.
    """

    task_id: TaskId


class TaskPanelToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `task_panel_toggled`. The task panel shows the current task."""


class LoadingModalToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `loading_modal_toggled`."""


class FeedbackPanelToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `feedback_panel_toggled`.

    Attributes:
        assessment_id: The assessment whose results are shown.
    """

    assessment_id: StrictInt = Field(ge=1)


class InfoModalToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `info_modal_toggled`."""


class DimensionModalToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `dimension_modal_toggled`.

    Attributes:
        dimension: The score dimension shown.
    """

    dimension: Dimension


class ScrollProperties(EventProperties):
    """Base model for `*_scrolled` events.

    Attributes:
        depth: How far the scroll has gone, 0 to 1.
    """

    depth: ScrollDepth


class ProfilePanelScrolledProperties(ScrollProperties):
    """Properties for `profile_panel_scrolled`, sent once a scroll comes to rest."""


class FeedbackPanelScrolledProperties(ScrollProperties):
    """Properties for `feedback_panel_scrolled`, sent once a scroll comes to rest.

    Attributes:
        task_id: The task on screen.
    """

    task_id: TaskId


class ButtonClickedProperties(EventProperties):
    """Base model for button clicks.

    Attributes:
        button_id: The button clicked.
    """

    button_id: Button


class MicrophoneButtonClickedProperties(ButtonClickedProperties):
    """Properties for `mic_button_clicked`: record, stop or replay on the task panel.

    Attributes:
        task_id: The task on screen.
    """

    task_id: TaskId


class EventName(StrEnum):
    """Accepted event names."""

    ASA_SESSION_ENDED = "asa_session_ended"
    ASA_SESSION_STARTED = "asa_session_started"
    MIC_BUTTON_CLICKED = "mic_button_clicked"
    DIMENSION_MODAL_TOGGLED = "dimension_modal_toggled"
    FEEDBACK_PANEL_SCROLLED = "feedback_panel_scrolled"
    FEEDBACK_PANEL_TOGGLED = "feedback_panel_toggled"
    INFO_MODAL_TOGGLED = "info_modal_toggled"
    LOADING_MODAL_TOGGLED = "loading_modal_toggled"
    PROFILE_PANEL_SCROLLED = "profile_panel_scrolled"
    PROFILE_PANEL_TOGGLED = "profile_panel_toggled"
    TASK_PANEL_TOGGLED = "task_panel_toggled"
    TASK_SELECTION_PANEL_TOGGLED = "task_selection_panel_toggled"


EVENT_PROPERTY_MODELS: dict[EventName, type[EventProperties]] = {
    EventName.ASA_SESSION_ENDED: ASASessionEndedProperties,
    EventName.ASA_SESSION_STARTED: ASASessionStartedProperties,
    EventName.MIC_BUTTON_CLICKED: MicrophoneButtonClickedProperties,
    EventName.DIMENSION_MODAL_TOGGLED: DimensionModalToggledProperties,
    EventName.FEEDBACK_PANEL_SCROLLED: FeedbackPanelScrolledProperties,
    EventName.FEEDBACK_PANEL_TOGGLED: FeedbackPanelToggledProperties,
    EventName.INFO_MODAL_TOGGLED: InfoModalToggledProperties,
    EventName.LOADING_MODAL_TOGGLED: LoadingModalToggledProperties,
    EventName.PROFILE_PANEL_SCROLLED: ProfilePanelScrolledProperties,
    EventName.PROFILE_PANEL_TOGGLED: ProfilePanelToggledProperties,
    EventName.TASK_PANEL_TOGGLED: TaskPanelToggledProperties,
    EventName.TASK_SELECTION_PANEL_TOGGLED: TaskSelectionPanelToggledProperties,
}


class _EventFields(BaseModel):
    """Fields every event shares.

    Attributes:
        event_id: Client-generated event ID, reused on retries.
        occurred_at: When the event happened, with a timezone.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    # Naive timestamps are ambiguous, so a timezone is required.
    occurred_at: AwareDatetime


class IncomingEvent(_EventFields):
    """Event payload, validated separately so it can be rejected alone.

    Attributes:
        name: The event name, checked against EventName.
        properties: Event properties, checked against the model for its name.
    """

    name: str = Field(min_length=1, max_length=EVENT_NAME_MAX_LENGTH)
    properties: dict[str, Any] = Field(default_factory=dict)


# Schema-only models, one per event, so OpenAPI shows each event's properties. Requests
# never validate against them: that would reject the whole batch for one bad event.
_EVENT_SCHEMAS = tuple(
    create_model(
        model.__name__.removesuffix("Properties") + "Event",
        __base__=_EventFields,
        __doc__=f"The `{name.value}` event.",
        # A Literal built from a variable is not a static type form, so type checkers
        # match no create_model overload without the cast. Pydantic reads it fine.
        name=(cast(Any, Literal[name.value]), ...),
        properties=(model, ...),
    )
    for name, model in EVENT_PROPERTY_MODELS.items()
)


def _require_object(value: Any) -> dict[str, Any]:
    """Accept any JSON object, leaving its validation to validate_events."""

    if not isinstance(value, dict):
        raise ValueError("event must be an object")
    return value


_IncomingEventObject = Annotated[
    dict[str, Any],
    PlainValidator(
        _require_object,
        json_schema_input_type=Annotated[
            Union[_EVENT_SCHEMAS],
            Field(discriminator="name"),
        ],
    ),
]


class EventBatchRequest(BaseModel):
    """Events request payload.

    Any fault here, or an unknown key, is a 422.

    Attributes:
        guid: The user's GUID.
        asa_session_id: Client-generated when the ASA session starts, cleared when it ends.
        app_version: The app version.
        events: The events, validated one by one.
    """

    model_config = ConfigDict(extra="forbid")

    guid: UUID
    asa_session_id: UUID
    app_version: str = Field(min_length=1, max_length=32,
                             pattern=r"^[0-9A-Za-z.+-]+$")
    events: list[_IncomingEventObject] = Field(
        min_length=1, max_length=MAX_EVENTS_PER_BATCH)


class RejectionReason(StrEnum):
    """Event rejection reason enumeration. Logged on the server only, never sent to the client.

    - `INVALID_EVENT`: Malformed event (event_id, name, naive occurred_at, unknown key).
    - `UNKNOWN_EVENT`: Name not in EventName.
    - `INVALID_PROPERTIES`: Properties do not match the event's model.
    - `TIMESTAMP_OUT_OF_RANGE`: Over 30 days old or over 5 minutes ahead.
    - `FOREIGN_REFERENCE`: References another user's assessment.
    """

    INVALID_EVENT = "INVALID_EVENT"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    INVALID_PROPERTIES = "INVALID_PROPERTIES"
    TIMESTAMP_OUT_OF_RANGE = "TIMESTAMP_OUT_OF_RANGE"
    FOREIGN_REFERENCE = "FOREIGN_REFERENCE"


class RejectedEvent(BaseModel):
    """Internal record of a rejected event, for the server log only.

    Holds nothing the client sent as free text: names outside EventName and the values
    that failed validation are left out.

    Attributes:
        index: The event's position in the request's `events`.
        event_id: The event's ID, or None when the ID itself was invalid.
        reason: Why the event was rejected.
        name: The event name, when it is a known one.
        fields: Which fields failed and how, e.g. `task_id: missing`.
    """

    index: int
    event_id: UUID | None
    reason: RejectionReason
    name: EventName | None = None
    fields: list[str] = Field(default_factory=list)


class ValidatedEvent(BaseModel):
    """Internal model for an event that passed validation."""

    index: int
    event_id: UUID
    name: EventName
    occurred_at: datetime
    properties: EventProperties
    # Ownership is checked in the insert transaction.
    assessment_ids: set[int] = Field(default_factory=set)


class CreateEventInput(BaseModel):
    """Internal DB input for one event row."""

    index: int
    event_id: UUID
    name: EventName
    properties_json: str
    occurred_at: str
    assessment_ids: set[int] = Field(default_factory=set)


class CreateEventsInput(BaseModel):
    """Internal DB input for one batch (one transaction)."""

    guid: UUID
    asa_session_id: UUID
    app_version: str
    events: list[CreateEventInput]


class CreateEventsResult(BaseModel):
    """Internal DB result for one batch insert."""

    inserted: int
    duplicates: int
    # Request indices rejected for referencing another user's assessment.
    foreign_reference_indices: list[int]
