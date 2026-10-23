import json
from collections import Counter
from datetime import datetime, timezone

from fastapi import Response

from app.db import create_events
from app.models.events import (
    CreateEventInput,
    CreateEventsInput,
    CreateEventsResult,
    EventBatchRequest,
    RejectedEvent,
    RejectionReason,
    ValidatedEvent,
)
from app.utils.logger import get_logger
from app.validators import auth
from app.validators.events import validate_events

logger = get_logger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time. A function so tests can freeze it."""

    return datetime.now(timezone.utc)


def _to_db_input(event: ValidatedEvent) -> CreateEventInput:
    """Serialise a validated event for storage.

    Properties come from the validated model, never the raw request. `occurred_at`
    matches `received_at`'s format, plus milliseconds.
    """

    return CreateEventInput(
        index=event.index,
        event_id=event.event_id,
        name=event.name,
        properties_json=json.dumps(
            # Unset optional keys are left out, so a row holds only what was sent.
            event.properties.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
        ),
        occurred_at=event.occurred_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        assessment_ids=event.assessment_ids,
    )


def record_events(data: EventBatchRequest) -> Response:
    """Validate and store a batch of events, rejecting invalid ones individually.

    Rejections are logged here and never sent back: the client only learns that the
    batch arrived, and drops it from its buffer.

    Args:
        data: Events payload including user GUID and the raw events.

    Returns:
        Response: 202 with no body.
    """

    auth.validate_user_access(data.guid)

    valid, rejected = validate_events(data.events, now=_utcnow())

    result = CreateEventsResult(
        inserted=0, duplicates=0, foreign_reference_indices=[])
    if valid:
        result = create_events(CreateEventsInput(
            guid=data.guid,
            asa_session_id=data.asa_session_id,
            app_version=data.app_version,
            events=[_to_db_input(event) for event in valid],
        ))

    by_index = {event.index: event for event in valid}
    rejected.extend(
        RejectedEvent(index=i, event_id=by_index[i].event_id, name=by_index[i].name,
                      reason=RejectionReason.FOREIGN_REFERENCE)
        for i in result.foreign_reference_indices
    )
    rejected.sort(key=lambda item: item.index)

    # Never log the guid, the session, property values or the body.
    logger.info(
        "Stored events: accepted=%d duplicates=%d rejected=%d app_version=%s",
        result.inserted, result.duplicates, len(rejected), data.app_version,
    )
    for item in rejected:
        logger.warning(
            "Rejected event: index=%d reason=%s name=%s event_id=%s fields=%s",
            item.index,
            item.reason.value,
            item.name.value if item.name else "-",
            item.event_id or "-",
            "; ".join(item.fields) or "-",
        )
    if rejected:
        reasons = Counter(item.reason.value for item in rejected)
        logger.info(
            "Rejected events by reason: %s",
            ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())),
        )

    return Response(status_code=202)
