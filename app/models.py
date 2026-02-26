from pydantic import BaseModel


class SpeechAssessmentScore(BaseModel):
    """Speech assessment score type"""

    accuracy: float
    fluency: float
    holistic: float
    pronunciation: float
    range_score: float


class SpeechAssessment(BaseModel):
    """Speech assessment response type"""

    score: SpeechAssessmentScore
    transcript: str
