"""Prompt construction — must reproduce training byte for byte.

The trained checkpoint saw exactly one prompt shape. It is reproduced here rather than
imported because the repo builds it through a pandas Dataset bound to the training CSVs.
scripts/dta_production/test_input_parity.py (research repo) checks this module against the
tokenized prompts in the training feature cache for all 98 held-out test recordings, so
"byte for byte" is verified, not asserted.

Shape (the checkpoint was trained with --no_acoustic_feats, so there is NO
<ACOUSTIC_EVIDENCE> block -- fluency and pronunciation are sensed from the Whisper frames,
not spoon-fed to the LLM as text):

    {rubric}\\n\\n{llm_input}{score_cue}

where llm_input is  <TASK task_id=.. task_name=..>\\nQ1: {prompt}\\nA1: {transcript}
"""
from .config import SCORE_CUE, TARGET_LANGUAGE

# data_loaders/sandi_dataset.py DEFAULT_RUBRIC, with target_language substituted. Copied
# verbatim; a single changed character invalidates the parity test and the checkpoint.
_RUBRIC_TEMPLATE = (
    "<RUBRIC>\n"
    "target_language: {target_language}\n"
    "You are scoring ONE part of an L2 speaking test on the CEFR scale, giving a single "
    "holistic CEFR score for the whole part.\n"
    "A Whisper-medium speech encoder with a LoRA adapter has already assessed the ACOUSTIC and "
    "DELIVERY of this response — fluency, pronunciation, intonation, hesitation and speech rate. "
    "Its judgement (supplied above as the acoustic soft tokens and the `acoustic_cefr_estimate` "
    "value) is reliable ONLY for that acoustic/delivery aspect — it can hear how the speech sounds "
    "but it cannot read what was actually said, so its CEFR estimate is just an acoustic-based "
    "guess.\n"
    "Your job is to determine the TRUE overall CEFR score. Take the acoustic estimate as the "
    "delivery signal, then compensate using the CONTENT of the response, which only you can judge "
    "in the target language from the transcript: vocabulary range, grammatical range and "
    "accuracy, coherence, and how fully each question/task is addressed. Raise the score when "
    "the content is richer or more accurate than the delivery alone implies, lower it when the "
    "content is weak, and output one holistic CEFR score for the part.\n"
    "The transcript below is produced by automatic speech recognition (ASR) and may contain "
    "recognition errors; judge the content through them."
)

RUBRIC = _RUBRIC_TEMPLATE.format(target_language=TARGET_LANGUAGE)

# prompts/finnish_asr_content_v1_finnishv3.json "input_template"
TASK_TEMPLATE = "<TASK task_id={task_id} task_name={task_name}>\nQ1: {question}\nA1: {answer}"


def normalise_text(value) -> str:
    """The repo's `_text()`: collapse all whitespace runs to single spaces, strip.

    Applied to both the task prompt and the ASR transcript when the training masters were
    built, so a transcript containing a newline must be normalised the same way here.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())


def build_llm_input(task_id: str, task_name: str, question: str, answer: str) -> str:
    return TASK_TEMPLATE.format(
        task_id=task_id,
        task_name=task_name,
        question=normalise_text(question),
        answer=normalise_text(answer),
    )


def build_prompt(llm_input: str) -> str:
    """FinnishMultiDimDataset._build_prompt with no_acoustic_feats=True and no content analysis."""
    return f"{RUBRIC}\n\n{llm_input}{SCORE_CUE}"
