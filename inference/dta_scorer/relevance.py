"""Content-relevance judge: does the answer address the task it was given?

The scorer rates HOW someone speaks, not WHAT they said. A learner who speaks fluent
Finnish about the wrong subject gets a good score for an answer that never addressed the
task. This module adds the missing channel: a zero-shot judge that reads the task prompt
and the ASR transcript and returns one of three verdicts.

    on_topic / partial / off_topic          (+ a probability, + a fixed reason string)

It is a SIDE CHANNEL. It never touches `cefr` or `dimensions` -- the calibrated score is
computed and returned exactly as it was before this file existed. The application tier
decides what to do with the verdict.

WHAT RUNS IT. The Qwen already resident in the scorer, with the LoRA adapter DISABLED, i.e.
plain Qwen3.5-2B base weights. Two consequences worth being explicit about:

  * No extra VRAM and no second model load. The judge is a few hundred milliseconds of
    prefill on weights that are already on the GPU.
  * The adapter is off because the scoring LoRA was trained to emit a number at <SCORE>:,
    not to answer questions. Judging with the adapter on would be reading a regression head
    sideways. `disable_adapter()` gives back the base model the judge prompt was designed
    for -- and since LoRA training leaves base weights frozen, those are the same weights
    the research repo's content_validation.py used. Verified rather than assumed: with a
    live adapter in place, the same on-topic answer flips from p(good)=0.96 to p(bad)=0.93,
    and `disable_adapter()` restores the bare-model probabilities exactly.

PROVENANCE. The system prompt and the good/average/bad label set are the research repo's
(aalto-speech/CASA `content_validation.py`, `prompts/content_validation.json`), which was
run over the clean test master and over deliberately attacked masters (every answer paired
with a question about nuclear reactors) to check the judge separates them. Two DTA-specific
deviations, both deliberate:

  1. The few-shot examples here are FINNISH and shaped like real DTA tasks. CASA's are
     English SANDI-shaped. They are hand-authored illustrations, not measured data.
  2. An added instruction that weak language is not the same as a wrong topic (see the
     LOW-PROFICIENCY GUARD below).

Because of (1) and (2) the paper's measured label distributions do not transfer to this
prompt. Treat the verdict as a useful signal that has not yet been validated against a
labelled Finnish off-topic set -- collect `content_relevance` from production, label a
sample, and measure before anyone lets `off_topic` block a learner outright.

WHAT WAS MEASURED (14 hand-written cases on the production P100, 2026-08-09; a smoke test,
not a validation set):

  * No genuinely on-topic answer was called `bad`. The worst was an A1-level, barely
    grammatical but on-topic answer at p(bad)=0.33 -- well under the 0.6 threshold.
  * Blatant off-topic answers land at p(bad) 0.78-0.95: an answer about a nuclear reactor
    (the research repo's own attack shape), a weather monologue, Whisper's silence
    hallucination ("Tekstitys: Yle"), and English speech force-decoded as Finnish.
  * KNOWN MISS: a NEIGHBOURING topic is not separated. A shopping answer given to the
    "what do you do at home" task scored p(good)=0.48 and passed as on-topic. The judge
    catches wrong subjects, not wrong tasks within the same everyday domain.
  * ~660 ms per judgement (1096-token prompt, one prefill, fp16 autocast). That is the
    per-request cost of the feature, on top of ~1.7 s (5 s audio) to ~8.3 s (90 s audio).
    If it ever needs to be cheaper: the few-shot prefix is ~1000 of those tokens and is
    identical every time, so its KV cache could be built once at warmup and reused.

LOW-PROFICIENCY GUARD. The failure that matters here is not a missed off-topic answer, it
is an A1 learner whose halting, error-filled but perfectly on-topic answer gets called
off-topic and withheld. That would punish exactly the users the app exists for. Three
things guard against it: the system prompt says so in as many words, one few-shot example
is a barely-grammatical answer labelled `good`, and `off_topic` is demoted to `partial`
unless the judge's probability clears DTA_RELEVANCE_OFF_TOPIC_MIN_CONFIDENCE.

DECODING. `good`, `average` and `bad` are each a single Qwen token, so the verdict is a
softmax over three logits at one position -- one forward pass, no generation loop. That is
the same answer CASA's constrained greedy decode produces (an argmax over the same three
token logits), a few hundred ms cheaper, and it yields a real probability rather than a
number invented to fill a `confidence` field.
"""
import contextlib
import os
import re

import torch

from .prompt import build_llm_input, normalise_text

# --- knobs (runtime only; none of this can move a score) --------------------------------
ENABLED = os.environ.get("DTA_RELEVANCE_CHECK", "1") == "1"
# An off_topic verdict is the destructive one -- the app withholds the score on it. Make it
# clear a bar that `partial` (which only annotates) does not have to. 0.6 sits in the gap
# measured above: on-topic answers peaked at p(bad)=0.33, blatant off-topic ones sat at
# 0.78+. The cost of the two errors is not symmetric -- withholding a real learner's score
# is much worse than missing an off-topic one -- so a borderline case (one probe case at
# 0.49) becomes `partial` rather than being flagged. Raise it to make the feature more
# cautious still; lower it only against labelled production data.
OFF_TOPIC_MIN_CONFIDENCE = float(
    os.environ.get("DTA_RELEVANCE_OFF_TOPIC_MIN_CONFIDENCE", "0.6"))
# 90 s of speech is ~200 words; the cap only bites on a runaway ASR repetition loop.
MAX_ANSWER_CHARS = int(os.environ.get("DTA_RELEVANCE_MAX_ANSWER_CHARS", "2000"))

VERSION = "dta-relevance-v1"

# Most DTA prompts open with ~150 characters of recording instructions that are identical
# across tasks and are not part of what the candidate must talk about ("Teet 2
# monologitehtävää. ... Klikkaa "Start recording" ja puhu. Sinulla on 1 minuutti aikaa.").
# Feeding them to the judge dilutes the actual question, measurably: on the P100, stripping
# them moved the nuclear-reactor attack case from p(bad)=0.54 to 0.84 and a terse but valid
# answer from `partial` to `on_topic`. Non-matching prompts are used verbatim.
#
# THIS APPLIES TO THE JUDGE ONLY. The scorer must keep receiving prompt_fi byte for byte --
# that is a training-parity requirement (see prompt.py), and changing it changes the scores.
_BOILERPLATE = re.compile(r"^.*?Sinulla on [^.]*aikaa\.\s*", re.S)


def task_question(task) -> str:
    """The part of the task prompt that states what to talk about."""
    return _BOILERPLATE.sub("", task.prompt_fi) or task.prompt_fi

# label token -> what the API calls it. The judge's own vocabulary is kept because the
# prompt that was validated is written in it; the mapping happens at the boundary.
_LABELS = {"good": "on_topic", "average": "partial", "bad": "off_topic"}

# Fixed strings, not LLM free text. A 2B base model asked to explain itself produces
# unreliable English prose that the app cannot localise, so `reason` is keyed to the
# verdict and the app translates off `relevance`.
_REASONS = {
    "on_topic": None,
    "partial": "The answer is on the topic of the task but only partly addresses it.",
    "off_topic": "The answer does not address the task that was asked.",
}
_NO_SPEECH_REASON = "No speech could be recognised in the recording."

_SYSTEM = (
    # --- verbatim from CASA prompts/content_validation.json -----------------------------
    "You are an examiner for a spoken language exam. You are given the exam TASK (the "
    "question / prompt) and the candidate's spoken answer, transcribed by ASR (which may "
    "contain recognition errors). Judge ONLY how well the answer ADDRESSES the task — its "
    "topical relevance and task fulfilment. Do NOT judge grammar, vocabulary, pronunciation "
    "or fluency. Reply with exactly one word:\n"
    "  good = the answer clearly addresses the question / task and stays on topic;\n"
    "  average = on the general topic but incomplete, drifts, or only loosely addresses the "
    "task;\n"
    "  bad = does not address the question, is off-topic, or is non-responsive.\n"
    # --- DTA addition: the low-proficiency guard ----------------------------------------
    "\nThe exam is in Finnish and the candidates are learners at CEFR A1–B1. A short, "
    "hesitant, or heavily error-filled answer that is still ABOUT the task is good or "
    "average, never bad: weak language is not a wrong topic. Answer bad only when the "
    "content is about something else, is empty, or is not a response to this task at all."
)

# Hand-authored, using the real Finnish task questions from assets/tasks.json so the block
# the judge sees at inference time has the same shape as the ones it sees here -- including
# the boilerplate stripping above, hence no "Klikkaa Start recording" lines. Written as our
# Finnish Whisper actually transcribes learner speech (punctuated, filler words kept).
_FEWSHOT = [
    # 1. Clearly addresses the task.
    ("04_h", "dta-task4_a",
     "Kerro, mitä kaikkea sinä teet normaalisti kotona.",
     "Minä asun Espoossa vaimon kanssa. Kotona minä siivoan ja teen ruokaa melkein joka "
     "päivä. Aamulla juon kahvia ja luen uutisia, ja illalla katson televisiota tai luen "
     "kirjaa. Viikonloppuna minä pesen pyykkiä ja joskus leivon pullaa.",
     "good"),
    # 2. THE LOW-PROFICIENCY GUARD. Barely grammatical, one-word-at-a-time, and it still
    #    answers all three questions. This example exists so the judge does not learn to
    #    read "weak" as "off topic".
    ("04_test", "dta-task5",
     "Mitä sinä normaalisti ostat kaupasta? Mitä sinä et normaalisti osta kaupasta? "
     "Milloin sinä normaalisti käyt kaupassa?",
     "Öö... minä ostaa maito ja leipä. Ja... öö... omena, banaani. Minä ei osta liha, ei "
     "hyvä. Minä menee kauppa lauantai.",
     "good"),
    # 3. Starts on task, then drifts into an unrelated anecdote.
    ("03_n", "dta-task2_b",
     "Sinä et voi tulla tänään kurssille/töihin. Sinä lähetät ääniviestin "
     "opettajalle/pomolle ja kerrot, miksi sinä et voi tulla. Mitä sinä sanot viestissä?",
     "Moi opettaja, minä olen vähän kipeä tänään. Ai niin, viime viikolla minä kävin "
     "Tallinnassa laivalla ja siellä oli tosi kivaa, me syötiin ravintolassa ja ostettiin "
     "suklaata, ja laivalla oli musiikkia ja tanssia.",
     "average"),
    # 4. Fluent, well-formed, and about something else entirely -- the case the whole
    #    feature exists for.
    ("04_i", "dta-task4_b",
     "Kerro, mitä kieliä sinä käytät ja missä. Mitä kieliä sinä puhut kotona, kaupassa, "
     "työssä...? Katsotko televisiota tai kuunteletko musiikkia: mitä kieliä sinä kuulet? "
     "Entä mitä kieliä luet tai kirjoitat?",
     "Viikonloppuna minä pelaan jalkapalloa kavereiden kanssa ja sitten me katsomme "
     "elokuvia. Sunnuntaina minä nukun pitkään ja syön pizzaa ja käyn kuntosalilla.",
     "bad"),
    # 5. Whisper's subtitle-credit hallucination, which is what near-silence transcribes to.
    ("03_m", "dta-task2_a",
     "Sinun ystävällä ei ole paljon rahaa, ja hän pyytää sinulta 100 euroa. Mitä sinä "
     "vastaat hänelle?",
     "Tekstitys: Yle. Kiitos kun katsoit videon!",
     "bad"),
    # 6. English speech decoded by a Finnish-forced Whisper. ASR_LANGUAGE is pinned to "fi"
    #    (asr.py), so the decoder never switches language -- it renders English phonetically
    #    as Finnish word salad. Illustrative, not a captured production transcript.
    ("04_test", "dta-task5",
     "Mitä sinä normaalisti ostat kaupasta? Mitä sinä et normaalisti osta kaupasta? "
     "Milloin sinä normaalisti käyt kaupassa?",
     "Ai juusuali kou tuu se maaketti ja ai bai som milkki ja bredi ja se on se ja se ja "
     "mitä mitä on se viikonloppu ja ja ja.",
     "bad"),
]


def _has_speech(text: str) -> bool:
    """Anything with a letter or digit in it counts. Silence transcribes to "" or "...".

    Deliberately generous: a one-word answer is a real (if weak) answer and must reach the
    judge rather than being called off-topic by a length threshold.
    """
    return any(ch.isalnum() for ch in text)


class RelevanceJudge:
    """Zero-shot topical-relevance judge over the scorer's own Qwen, adapter disabled.

    Construct once, alongside the pipeline. `judge()` is called inside the pipeline's lock,
    so it inherits the same one-request-at-a-time serialisation as scoring.
    """

    def __init__(self, scorer, device: str = "cuda"):
        self.device = device
        self.qwen = scorer.model.qwen        # peft-wrapped AutoModelForCausalLM
        self.tokenizer = scorer.tokenizer
        self.autocast_dtype = scorer.autocast_dtype
        self._prefix = self._build_prefix()

        ids = {}
        for word in _LABELS:
            token = self.tokenizer(word, add_special_tokens=False)["input_ids"]
            if len(token) != 1:
                # Would silently break the single-forward argmax below, so refuse loudly
                # rather than judge on a truncated first token.
                raise RuntimeError(
                    f"label {word!r} is not a single token for this tokenizer ({token}); "
                    "relevance judging needs single-token labels")
            ids[word] = token[0]
        self.label_ids = ids
        self._label_id_tensor = torch.tensor(
            [ids[w] for w in _LABELS], dtype=torch.long, device=device)
        self._label_words = list(_LABELS)

    def _build_prefix(self) -> list[dict]:
        messages = [{"role": "system", "content": _SYSTEM}]
        for task_id, task_name, question, answer, label in _FEWSHOT:
            block = build_llm_input(task_id=task_id, task_name=task_name,
                                    question=question, answer=answer)
            messages.append({"role": "user", "content": f"{block}\n\nRelevance:"})
            messages.append({"role": "assistant", "content": label})
        return messages

    def _render(self, task, transcript: str) -> str:
        block = build_llm_input(task_id=task.task_id, task_name=task.task_name,
                                question=task_question(task),
                                answer=normalise_text(transcript)[:MAX_ANSWER_CHARS])
        messages = self._prefix + [{"role": "user", "content": f"{block}\n\nRelevance:"}]
        try:
            return self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            # Older template without the thinking switch: the label is still the next token.
            return self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)

    @torch.inference_mode()
    def _label_probabilities(self, text: str) -> dict[str, float]:
        enc = self.tokenizer(text, return_tensors="pt", add_special_tokens=False)
        enc = {k: v.to(self.device) for k, v in enc.items()}
        use_amp = self.device.startswith("cuda") and self.autocast_dtype != torch.float32
        # LoRA OFF: the scoring adapter is a regression head, useless as a judge. The
        # nullcontext branch is for a bare (non-peft) causal LM -- there is no adapter to
        # disable, so the judge is already reading base weights.
        adapter_off = (self.qwen.disable_adapter() if hasattr(self.qwen, "disable_adapter")
                       else contextlib.nullcontext())
        with adapter_off:
            with torch.autocast("cuda", dtype=self.autocast_dtype, enabled=use_amp):
                # Only the last position is read. Without this the head materialises
                # seq_len x vocab (~0.9 GB fp32 at 1.5k tokens) on a GPU that is already
                # holding the scorer.
                out = self.qwen(**enc, use_cache=False, logits_to_keep=1)
        # Renormalised over the three labels only: the judge is not allowed to answer
        # anything else, so the mass on the rest of the vocabulary is not a third option.
        logits = out.logits[0, -1].float()
        probs = torch.softmax(logits.index_select(0, self._label_id_tensor), dim=-1)
        return dict(zip(self._label_words, probs.tolist()))

    def judge(self, task, transcript: str) -> dict:
        """Return the `content` block for one recording. Never raises.

        Callers treat a `None` return as "not checked" and show the score unannotated --
        the fail-open contract in docs/FRONTEND.md.
        """
        text = normalise_text(transcript)
        if not _has_speech(text):
            # No LLM call: an empty transcript is not a judgement call, and the judge would
            # be guessing from an empty answer field.
            return {"relevance": "off_topic", "confidence": 1.0,
                    "reason": _NO_SPEECH_REASON, "judge": VERSION}

        probs = self._label_probabilities(self._render(task, text))
        word = max(probs, key=probs.get)
        relevance = _LABELS[word]
        confidence = probs[word]

        if relevance == "off_topic" and confidence < OFF_TOPIC_MIN_CONFIDENCE:
            # Withholding a score is the one action here that costs the learner something.
            # An unsure judge annotates instead.
            relevance = "partial"
            confidence = probs["average"] + probs["bad"]

        return {"relevance": relevance, "confidence": round(float(confidence), 3),
                "reason": _REASONS[relevance], "judge": VERSION}
