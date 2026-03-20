from typing import Annotated

from pydantic import BaseModel, Field

Score = Annotated[float, Field(ge=0, le=5)]


class SpeechAssessmentScores(BaseModel):
    """Speech assessment score type"""

    accuracy: Score
    fluency: Score
    proficiency: Score
    pronunciation: Score
    range: Score


class SpeechAssessment(BaseModel):
    """Speech assessment result type"""

    scores: SpeechAssessmentScores
    transcript: str
