"""Response envelope models, published in the OpenAPI schema.

Every non-422 error is `{"detail": {"type": ..., "message": ...}}` (an object).
422 keeps the same object plus an `errors` array with the field-level issues.
These shapes are a commitment clients branch on -- see docs/FRONTEND.md -- and
before these models existed they lived only in prose, so a generated client
could not parse any non-422 error.
"""
from pydantic import BaseModel, Field

from app.error_handlers import ErrorType


class ErrorDetail(BaseModel):
    """The `detail` object every error response carries."""

    type: ErrorType
    message: str

    # Some errors carry extra machine-readable context next to type/message:
    # SCORING_UNAVAILABLE adds `reason` (starting_up | busy | unreachable);
    # FILE_TOO_LARGE adds size_bytes/max_size_bytes; AUDIO_TOO_LONG adds
    # duration_seconds/max_duration_seconds.
    model_config = {"extra": "allow"}


class ErrorEnvelope(BaseModel):
    """Error response body for every status except 422."""

    detail: ErrorDetail


class ValidationErrorDetail(ErrorDetail):
    """422 detail: the same object, plus field-level issues."""

    errors: list[dict] = Field(
        description="FastAPI/pydantic issues; each has type, loc, msg.")


class ValidationErrorEnvelope(BaseModel):
    """Error response body for 422 VALIDATION_ERROR."""

    detail: ValidationErrorDetail
