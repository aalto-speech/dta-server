from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from models import SpeechAssessment

app = FastAPI()


@app.get("/hello")
async def read_root():
    """Hello endpoint"""
    return "Hello world!"


@app.post("/speech/assess")
async def assess_speech() -> JSONResponse:
    """Assess speech using the AI model"""
    fluency = 4.3
    pronunciation = 2.4
    range_score = 3.3
    accuracy = 4.9
    holistic = 4.0

    json = jsonable_encoder(
        SpeechAssessment(
            fluency=fluency,
            pronunciation=pronunciation,
            range_score=range_score,
            accuracy=accuracy,
            holistic=holistic,
        )
    )
    return JSONResponse(content=json, status_code=200)
