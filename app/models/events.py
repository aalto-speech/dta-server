from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    WithJsonSchema,
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


class AppOpenedProperties(EventProperties):
    """Properties for `app_opened`."""


class ScreenViewedProperties(EventProperties):
    """Properties for `screen_viewed`.

    Attributes:
        screen: Screen identifier in lowercase snake_case.
    """

    screen: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]*$")


class ResultViewedProperties(EventProperties):
    """Properties for `result_viewed`.

    Attributes:
        assessment_id: The learner's own assessment whose result was opened.
    """

    # StrictInt rejects "5" and booleans.
    assessment_id: StrictInt = Field(ge=1)


class EventName(StrEnum):
    """Accepted event names."""

    APP_OPENED = "app_opened"
    SCREEN_VIEWED = "screen_viewed"
    RESULT_VIEWED = "result_viewed"


EVENT_PROPERTY_MODELS: dict[EventName, type[EventProperties]] = {
    EventName.APP_OPENED: AppOpenedProperties,
    EventName.SCREEN_VIEWED: ScreenViewedProperties,
    EventName.RESULT_VIEWED: ResultViewedProperties,
}


class IncomingEvent(BaseModel):
    """Event payload, validated separately so it can be rejected alone.

    Attributes:
        event_id: Client-generated event ID, reused on retries.
        name: The event name, checked against EventName.
        occurred_at: When the event happened, with a timezone.
        properties: Event properties, checked against the model for its name.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    name: Annotated[
        str,
        Field(min_length=1, max_length=EVENT_NAME_MAX_LENGTH),
        WithJsonSchema({"type": "string", "enum": [e.value for e in EventName]})]
    # Naive timestamps are ambiguous, so a timezone is required.
    occurred_at: AwareDatetime
    properties: dict[str, Any] = Field(default_factory=dict)


# Plain dicts so each event validates separately. OpenAPI still shows IncomingEvent.
_IncomingEventObject = Annotated[
    dict[str, Any],
    WithJsonSchema(IncomingEvent.model_json_schema()),
]


class EventBatchRequest(BaseModel):
    """Events request payload.

    Any fault here, or an unknown key, is a 422.

    Attributes:
        guid: The user's GUID.
        session_id: Client-generated ID for one app launch.
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
