import json
from collections import Counter
from datetime import datetime, timezone

from fastapi.responses import JSONResponse

from app.db import create_events
from app.models.events import (
    CreateEventInput,
    CreateEventsInput,
    CreateEventsResult,
    EventBatchRequest,
    EventBatchResponse,
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
            event.properties.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ),
        occurred_at=event.occurred_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        assessment_ids=event.assessment_ids,
    )


def record_events(data: EventBatchRequest) -> JSONResponse:
    """Validate and store a batch of events, rejecting invalid ones individually.

    Args:
        data: Events payload including user GUID and the raw events.

    Returns:
        JSONResponse: 202 with accepted, duplicate and rejected counts.
    """

    auth.validate_user_access(data.guid)

    valid, rejected = validate_events(data.events, now=_utcnow())

    result = CreateEventsResult(
        inserted=0, duplicates=0, foreign_reference_indices=[])
    if valid:
        result = create_events(CreateEventsInput(
            guid=data.guid,
            session_id=data.session_id,
            app_version=data.app_version,
            events=[_to_db_input(event) for event in valid],
        ))

    event_ids = {event.index: event.event_id for event in valid}
    rejected.extend(
        RejectedEvent(index=i, event_id=event_ids[i],
                      reason=RejectionReason.FOREIGN_REFERENCE)
        for i in result.foreign_reference_indices
    )
    rejected.sort(key=lambda item: item.index)

    # Counts only. Never log the guid, properties or the body.
    logger.info(
        "Stored events: accepted=%d duplicates=%d rejected=%d",
        result.inserted, result.duplicates, len(rejected),
    )
    if rejected:
        reasons = Counter(item.reason.value for item in rejected)
        logger.info(
            "Rejected events by reason: %s",
            ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())),
        )

    return JSONResponse(
        content=EventBatchResponse(
            accepted=result.inserted,
            duplicates=result.duplicates,
            rejected=rejected,
        ).model_dump(mode="json"),
        status_code=202,
    )
