from pydantic import BaseModel


class SpeechAssessmentScores(BaseModel):
    """Speech assessment score type"""

    accuracy: float
    fluency: float
    proficiency: float
    pronunciation: float
    range: float


class SpeechAssessment(BaseModel):
    """Speech assessment result type"""

    scores: SpeechAssessmentScores
    transcript: str
