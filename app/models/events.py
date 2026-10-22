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
    model_validator,
)

MAX_EVENTS_PER_BATCH = 100

# Outside this window: a device clock fault or a stale buffer.
MAX_EVENT_AGE = timedelta(days=30)
MAX_EVENT_CLOCK_SKEW = timedelta(minutes=5)

EVENT_NAME_MAX_LENGTH = 64


class EventProperties(BaseModel):
    """Base model for event properties.

    Every key is declared and bounded, so no free text can be stored. Tests enforce
    `extra="forbid"` and `max_length` on string fields.
    """

    model_config = ConfigDict(extra="forbid")


# StrictInt rejects "5" and booleans. 1-5 is the task table in docs/FRONTEND.md.
TaskId = Annotated[StrictInt, Field(ge=1, le=5)]

# Fraction of the panel scrolled past: 0 is the top, 1 the bottom. Unity's
# verticalNormalizedPosition runs the other way, so the client sends 1 minus it.
ScrollDepth = Annotated[float, Field(
    ge=0, le=1, strict=True, allow_inf_nan=False)]


class Button(StrEnum):
    """Accepted `button_id` values for `button_clicked`."""

    ASA = "asa"
    TASK = "task"
    PROFILE = "profile"
    REVERT_LEVEL = "revert_level"
    ADVANCE_LEVEL = "advance_level"
    RECORD = "record"
    STOP = "stop"
    REPLAY = "replay"
    RESULTS = "results"
    RESULTS_NEXT = "results_next"
    NEW_TASK = "new_task"


# A task button carries the task it opens; buttons under the ASA and feedback panels carry
# the task on screen. The rest are outside any task and must not carry one.
TASK_BUTTONS = frozenset({
    Button.TASK,
    Button.RECORD,
    Button.STOP,
    Button.REPLAY,
    Button.RESULTS,
    Button.RESULTS_NEXT,
    Button.NEW_TASK,
})


class Dimension(StrEnum):
    """Accepted `dimension` values, the score keys of POST /speech/assess."""

    PROFICIENCY = "proficiency"
    FLUENCY = "fluency"
    PRONUNCIATION = "pronunciation"
    RANGE = "range"
    ACCURACY = "accuracy"


class PanelToggledProperties(EventProperties):
    """Base model for `*_toggled` events.

    Attributes:
        active: Whether the panel is active after the toggle.
    """

    active: StrictBool


class TaskPanelToggledProperties(PanelToggledProperties):
    """Properties for `task_panel_toggled`."""


class ProfilePanelToggledProperties(PanelToggledProperties):
    """Properties for `profile_panel_toggled`."""


class TaskScopedPanelToggledProperties(PanelToggledProperties):
    """Base model for toggles under the ASA and feedback panels.

    Attributes:
        task_id: The task on screen.
    """

    task_id: TaskId


class ASAPanelToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `asa_panel_toggled`."""


class LoadingPanelToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `loading_panel_toggled`."""


class FeedbackPanelToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `feedback_panel_toggled`.

    Attributes:
        assessment_id: The assessment whose results are shown. Optional, so a missing
            ID costs the link to the scores rather than the event.
    """

    assessment_id: StrictInt | None = Field(default=None, ge=1)


class InfoPopupToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `info_popup_toggled`."""


class DimensionToggledProperties(TaskScopedPanelToggledProperties):
    """Properties for `dimension_toggled`.

    Attributes:
        dimension: The score dimension toggled.
    """

    dimension: Dimension


class ProfilePanelScrolledProperties(EventProperties):
    """Properties for `profile_panel_scrolled`, sent once a scroll comes to rest.

    Attributes:
        depth: Where the scroll came to rest.
    """

    depth: ScrollDepth


class FeedbackPanelScrolledProperties(ProfilePanelScrolledProperties):
    """Properties for `feedback_panel_scrolled`, sent once a scroll comes to rest.

    Attributes:
        task_id: The task on screen.
    """

    task_id: TaskId


class ButtonClickedProperties(EventProperties):
    """Properties for `button_clicked`.

    Attributes:
        button_id: The button clicked.
        task_id: The task. Required for `task`, `record`, `stop`, `replay`, `results`,
            `results_next` and `new_task`; not allowed for the rest.
    """

    button_id: Button
    task_id: TaskId | None = None

    @model_validator(mode="after")
    def _check_task_id(self) -> "ButtonClickedProperties":
        scoped = self.button_id in TASK_BUTTONS
        if (self.task_id is not None) != scoped:
            rule = "required" if scoped else "not allowed"
            raise ValueError(f"task_id is {rule} for button {self.button_id}")
        return self


class EventName(StrEnum):
    """Accepted event names."""

    TASK_PANEL_TOGGLED = "task_panel_toggled"
    PROFILE_PANEL_TOGGLED = "profile_panel_toggled"
    ASA_PANEL_TOGGLED = "asa_panel_toggled"
    LOADING_PANEL_TOGGLED = "loading_panel_toggled"
    FEEDBACK_PANEL_TOGGLED = "feedback_panel_toggled"
    INFO_POPUP_TOGGLED = "info_popup_toggled"
    DIMENSION_TOGGLED = "dimension_toggled"
    PROFILE_PANEL_SCROLLED = "profile_panel_scrolled"
    FEEDBACK_PANEL_SCROLLED = "feedback_panel_scrolled"
    BUTTON_CLICKED = "button_clicked"


EVENT_PROPERTY_MODELS: dict[EventName, type[EventProperties]] = {
    EventName.TASK_PANEL_TOGGLED: TaskPanelToggledProperties,
    EventName.PROFILE_PANEL_TOGGLED: ProfilePanelToggledProperties,
    EventName.ASA_PANEL_TOGGLED: ASAPanelToggledProperties,
    EventName.LOADING_PANEL_TOGGLED: LoadingPanelToggledProperties,
    EventName.FEEDBACK_PANEL_TOGGLED: FeedbackPanelToggledProperties,
    EventName.INFO_POPUP_TOGGLED: InfoPopupToggledProperties,
    EventName.DIMENSION_TOGGLED: DimensionToggledProperties,
    EventName.PROFILE_PANEL_SCROLLED: ProfilePanelScrolledProperties,
    EventName.FEEDBACK_PANEL_SCROLLED: FeedbackPanelScrolledProperties,
    EventName.BUTTON_CLICKED: ButtonClickedProperties,
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
        session_id: Client-generated ID for ASA panel launch.
        app_version: The app version.
        events: The events, validated one by one.
    """

    model_config = ConfigDict(extra="forbid")

    guid: UUID
    session_id: UUID
    app_version: str = Field(min_length=1, max_length=32,
                             pattern=r"^[0-9A-Za-z.+-]+$")
    events: list[_IncomingEventObject] = Field(
        min_length=1, max_length=MAX_EVENTS_PER_BATCH)


class RejectionReason(StrEnum):
    """Event rejection reason enumeration. Rejected events must not be retried.

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
    """A rejected event.

    Attributes:
        index: The event's position in the request's `events`.
        event_id: The event's ID, or null when the ID itself was invalid.
        reason: Why the event was rejected.
    """

    index: int
    event_id: UUID | None
    reason: RejectionReason


class EventBatchResponse(BaseModel):
    """Events response payload. The client drops all three from its buffer.

    Attributes:
        accepted: Number of events stored.
        duplicates: Number of events skipped as already stored.
        rejected: Events that were not stored, with the reason.
    """

    accepted: int
    duplicates: int
    rejected: list[RejectedEvent]


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
    session_id: UUID
    app_version: str
    events: list[CreateEventInput]


class CreateEventsResult(BaseModel):
    """Internal DB result for one batch insert."""

    inserted: int
    duplicates: int
    # Request indices rejected for referencing another user's assessment.
    foreign_reference_indices: list[int]
