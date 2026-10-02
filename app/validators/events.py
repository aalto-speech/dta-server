from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.models.events import (
    EVENT_PROPERTY_MODELS,
    MAX_EVENT_AGE,
    MAX_EVENT_CLOCK_SKEW,
    EventName,
    IncomingEvent,
    RejectedEvent,
    RejectionReason,
    ValidatedEvent,
)


def to_utc(value: datetime) -> datetime:
    """Normalise an aware datetime to UTC."""

    return value.astimezone(timezone.utc)


def validate_events(
    raw_events: list[dict[str, Any]],
    now: datetime,
) -> tuple[list[ValidatedEvent], list[RejectedEvent]]:
    """Validate each event separately, without the database.

    Ownership of referenced assessments is checked later, in the insert transaction.

    Args:
        raw_events: The batch's `events`, as parsed JSON objects.
        now: Current aware time, for the occurred_at window.

    Returns:
        tuple[list[ValidatedEvent], list[RejectedEvent]]: The valid events and the
            rejections, each carrying its request index.
    """

    accepted: list[ValidatedEvent] = []
    rejected: list[RejectedEvent] = []
    earliest = now - MAX_EVENT_AGE
    latest = now + MAX_EVENT_CLOCK_SKEW

    for i, raw in enumerate(raw_events):
        try:
            event = IncomingEvent.model_validate(raw)
        except ValidationError:
            rejected.append(RejectedEvent(
                index=i,
                event_id=_event_id_or_none(raw),
                reason=RejectionReason.INVALID_EVENT,
            ))
            continue

        try:
            name = EventName(event.name)
        except ValueError:
            rejected.append(RejectedEvent(
                index=i, event_id=event.event_id,
                reason=RejectionReason.UNKNOWN_EVENT))
            continue

        try:
            properties = EVENT_PROPERTY_MODELS[name].model_validate(
                event.properties)
        except ValidationError:
            rejected.append(RejectedEvent(
                index=i, event_id=event.event_id,
                reason=RejectionReason.INVALID_PROPERTIES))
            continue

        occurred_at = to_utc(event.occurred_at)
        if not earliest <= occurred_at <= latest:
            rejected.append(RejectedEvent(
                index=i, event_id=event.event_id,
                reason=RejectionReason.TIMESTAMP_OUT_OF_RANGE))
            continue

        assessment_id = getattr(properties, "assessment_id", None)
        accepted.append(ValidatedEvent(
            index=i,
            event_id=event.event_id,
            name=name,
            occurred_at=occurred_at,
            properties=properties,
            assessment_ids={
                assessment_id} if assessment_id is not None else set(),
        ))

    return accepted, rejected


def _event_id_or_none(raw: dict[str, Any]) -> UUID | None:
    """Return a malformed event's event_id, or None when it is not a valid UUID."""

    event_id = raw.get("event_id")
    if not isinstance(event_id, str):
        return None
    try:
        return UUID(event_id)
    except ValueError:
        return None
