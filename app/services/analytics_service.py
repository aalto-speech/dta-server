from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.db import get_cohort_stats
from app.models.analytics import ComparisonRequest, ComparisonResponse
from app.validators import auth


def get_comparison(data: ComparisonRequest) -> JSONResponse:
    """Build a cohort comparison response for an authenticated user."""

    auth.validate_user_access(data.guid)
    stats = get_cohort_stats(data.guid, data.days)

    if not stats:
        return JSONResponse(
            content={
                "status": "comparison_unavailable",
                "message": (
                    "Comparison statistics are not available for your cohorts size at this time."
                ),
            },
            status_code=200,
        )

    payload = ComparisonResponse(
        cefr_level=stats.cefr_level,
        cohort_size=stats.cohort_size,
        percentile=stats.percentile,
        rank=stats.rank,
    )

    return JSONResponse(content=jsonable_encoder(payload), status_code=200)
