import sqlite3
import tempfile
from random import uniform


import whisper
from fastapi import FastAPI, File, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .models import SpeechAssessment, SpeechAssessmentScores

app = FastAPI()

# Load whisper model once at startup
whisper_model = whisper.load_model("small")


@app.on_event("startup")
async def setup_database():
    """Initialize database on app startup."""

    conn = sqlite3.connect('speech_assessments.db')
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        transcript TEXT NOT NULL,
        accuracy REAL,
        fluency REAL,
        proficiency REAL,
        pronunciation REAL,
        range REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    conn.close()


@app.get("/ping")
async def ping() -> JSONResponse:
    """Ping-pong endpoint for checking if the server is running."""
    return JSONResponse(content={"message": "Pong!"}, status_code=200)


@app.post("/speech/assess")
async def assess_speech(file: UploadFile = File(...)) -> JSONResponse:
    """Assess speech using the AI model."""

    # Save uploaded file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
        content = await file.read()
        f.write(content)
        temp_path = f.name

    try:
        # Transcribe audio using whisper (force Finnish language for better accuracy)
        result = whisper_model.transcribe(temp_path, language="fi")
        text = result["text"]
        transcript = " ".join(text) if isinstance(text, list) else str(text)

        # Placeholder assessment scores (replace with actual ML logic)
        accuracy = round(uniform(0, 5), 1)
        fluency = round(uniform(0, 5), 1)
        proficiency = round(uniform(0, 5), 1)
        pronunciation = round(uniform(0, 5), 1)
        range_score = round(uniform(0, 5), 1)

        json = jsonable_encoder(
            SpeechAssessment(
                scores=SpeechAssessmentScores(
                    accuracy=accuracy,
                    fluency=fluency,
                    proficiency=proficiency,
                    pronunciation=pronunciation,
                    range=range_score,
                ),
                transcript=transcript
            )
        )
        return JSONResponse(content=json, status_code=200)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return JSONResponse(content={"error": str(e)}, status_code=500)
    finally:
        # Clean up temporary file
        import os  # pylint: disable=import-outside-toplevel
        os.unlink(temp_path)
