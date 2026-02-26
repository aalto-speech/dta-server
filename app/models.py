from pydantic import BaseModel


class SpeechAssessmentScores(BaseModel):
    """Speech assessment score type"""

    accuracy: float
    fluency: float
    holistic: float
    pronunciation: float
    range_score: float


class SpeechAssessment(BaseModel):
    """Speech assessment response type"""

    scores: SpeechAssessmentScores
    transcript: str
