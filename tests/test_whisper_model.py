from app.utils.whisper_model import get_transcriber


def test_get_transcriber_returns_model_transcribe(monkeypatch) -> None:
    """get_transcriber should expose the loaded model transcribe callable."""

    calls = []

    class FakeModel:
        """Test double for a whisper model."""

        def transcribe(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"text": "ok"}

    monkeypatch.setattr("app.utils.whisper_model._model", FakeModel())

    transcriber = get_transcriber()
    result = transcriber("/tmp/sample.wav", language="fi")

    assert result == {"text": "ok"}
    assert calls == [(("/tmp/sample.wav",), {"language": "fi"})]