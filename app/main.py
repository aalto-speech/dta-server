import tempfile
import whisper
from fastapi import FastAPI, File, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.models import SpeechAssessment

app = FastAPI()

# Load whisper model once at startup
whisper_model = whisper.load_model("small")


@app.get("/hello")
async def read_root():
    """Hello endpoint"""
    return "Hello world!"


@app.post("/speech/assess")
async def assess_speech(file: UploadFile = File(...)) -> JSONResponse:
    """Assess speech using the AI model"""
    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
        content = await file.read()
        temp_file.write(content)
        temp_path = temp_file.name

    try:
        # Transcribe audio using whisper
        result = whisper_model.transcribe(temp_path)
        transcript = result["text"]

        # Placeholder assessment scores (replace with actual ML logic)
        fluency = 4.3
        pronunciation = 2.4
        range_score = 3.3
        accuracy = 4.9
        holistic = 4.0

        json = jsonable_encoder(
            SpeechAssessment(
                transcript=transcript,
                fluency=fluency,
                pronunciation=pronunciation,
                range_score=range_score,
                accuracy=accuracy,
                holistic=holistic,
            )
        )
        return JSONResponse(content=json, status_code=200)
    finally:
        # Clean up temporary file
        import os
        os.unlink(temp_path)
