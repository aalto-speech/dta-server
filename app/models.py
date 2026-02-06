from pydantic import BaseModel


class SpeechAssessment(BaseModel):
    """Speech assessment response type"""

    fluency: float
    pronunciation: float
    range_score: float
    accuracy: float
    holistic: float
