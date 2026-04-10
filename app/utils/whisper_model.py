import whisper

# Load whisper model once at startup
_model = whisper.load_model("small")


def get_transcriber():
    """Get the whisper transcriber function.

    Returns:
        The transcribe method of the loaded whisper model.
    """
    return _model.transcribe
