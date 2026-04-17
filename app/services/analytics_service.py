from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.db import get_cohort_stats
from app.models.analytics import (
    ComparisonUnavailable,
    ComparisonRequest,
    ComparisonResponse,
    GetCohortStatsInput,
)
from app.validators import auth


def get_comparison(data: ComparisonRequest) -> JSONResponse:
    """Build a cohort comparison response for an authenticated user.

    Args:
        data: Comparison request payload including user GUID and window options.

    Returns:
        JSONResponse: 200 with percentile/rank data or comparison unavailable status.
    """

    auth.validate_user_access(data.guid)
    stats = get_cohort_stats(GetCohortStatsInput(
        guid=data.guid, days=data.days))

    if isinstance(stats, ComparisonUnavailable):
        return JSONResponse(
            content=jsonable_encoder(stats, exclude_none=True),
            status_code=200,
        )

    payload = ComparisonResponse(
        cefr_level=stats.cefr_level,
        cohort_size=stats.cohort_size,
        percentile=stats.percentile,
        rank=stats.rank,
    )

    return JSONResponse(content=jsonable_encoder(payload), status_code=200)
