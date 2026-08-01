"""Finnish ASR with the project's own fine-tuned Whisper (finnish-v3).

Settings are copied from scripts/run_asr.py because the SCORER was trained on transcripts
produced exactly this way. Changing the decode settings changes the text distribution the
content head learned to read — a different ASR is a different input domain, and the project
has already measured that (a verbatim-ASR variant degraded the content channel on accented
L2 speech even though its raw CER looked competitive).

Held-out DTA-test CER for this model: 0.112.
"""
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from .config import (ASR_CHUNK_LENGTH_S, ASR_LANGUAGE, ASR_STRIDE_LENGTH_S, ASR_TASK,
                     SAMPLE_RATE, WHISPER_DIR)


class FinnishASR:
    def __init__(self, model_dir=WHISPER_DIR, device: str = "cuda",
                 dtype: torch.dtype = torch.float16):
        # fp16 on GPU matches run_asr.py; on CPU force fp32 (fp16 CPU matmul is unsupported
        # or catastrophically slow depending on the build).
        if device == "cpu":
            dtype = torch.float32
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            str(model_dir), dtype=dtype, low_cpu_mem_usage=True)
        processor = AutoProcessor.from_pretrained(str(model_dir))
        self.processor = processor
        self.pipe = pipeline(
            "automatic-speech-recognition",
            model=model, tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            dtype=dtype, device=device,
        )

    @property
    def feature_extractor(self):
        """Shared with the scorer's acoustic branch — same Whisper, same mel front end."""
        return self.processor.feature_extractor

    def transcribe(self, wave) -> str:
        out = self.pipe(
            {"raw": wave, "sampling_rate": SAMPLE_RATE},
            generate_kwargs={"language": ASR_LANGUAGE, "task": ASR_TASK},
            chunk_length_s=ASR_CHUNK_LENGTH_S,
            stride_length_s=ASR_STRIDE_LENGTH_S,
            ignore_warning=True,
        )
        return str(out["text"]).strip()
