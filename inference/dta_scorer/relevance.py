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
prompt.

WHY v2 EXISTS -- v1 FAILED ON REAL LEARNERS. v1 was tuned on 14 hand-written cases and
looked fine. Then nine real recordings arrived: five from an A1 speaker, four from an A2+
speaker, every one of them a genuine attempt at the task it was given, confirmed by the app
owner. v1 called **six of the nine** `partial` or `off_topic`, including a textbook answer
to "what do you normally do at home" that was flagged `off_topic` at 0.77 and had its
scores destroyed (the app zeroed them in v1.2.0). The two it got right scraped through at
0.50 and 0.56.

The hand-written cases missed it because they were clean Finnish. Real ASR output for real
learner speech is not clean, and the diagnosis was two-fold:

  * The verdict tracked ANSWER LENGTH and ASR DAMAGE, not topic. The same on-topic content
    scored p(bad)=0.59 at three words and 0.02 at twenty, and when Whisper rendered "sata
    euroa" as "sata ilva" the judge lost the thread entirely. Both are proxies for
    proficiency, so the check misfired precisely on the learners the app exists for.
  * It was grading COMPLETENESS. Fluent A2+ answers that fully addressed their task came
    back `average` at 0.46-0.86 -- "incomplete" was doing all the work, and relevance does
    not care whether every sub-question was covered.

v2 adds one few-shot example of ASR-mangled but on-topic speech (#3 below) and one
paragraph telling the judge to ignore completeness. Measured on the same nine recordings
plus seven genuine off-topic controls, on the production P100:

                          real recordings wrongly flagged    genuine off-topic caught
    v1 (shipped)                       6 of 9 (1 destroyed)          6 of 7
    v2                                 1 of 9 (0 destroyed)          6 of 7

  * Detection power is UNCHANGED: hallucinations still land at 0.91-0.96, English salad at
    0.80, a wrong subject at 0.75, and an answer to a different DTA task at 0.72.
  * The one remaining miss is recording 30 -- the most heavily ASR-mangled of the nine --
    now `partial` at 0.76 rather than `off_topic`, so it annotates instead of destroying.
  * A rejected candidate is worth recording: adding a SECOND mangled example fixed
    recording 30 but broke the "answered a different DTA task" control, dropping detection
    to 5 of 7. One example generalises; two overfit to the tuning set.
  * KNOWN MISS, unchanged from v1: a NEIGHBOURING topic. A weekend-activities answer given
    to the "which languages do you use" task passes. The judge catches wrong subjects, not
    wrong tasks within the same everyday domain.
  * ~660 ms per judgement (one prefill, fp16 autocast), on top of ~1.7 s (5 s audio) to
    ~8.3 s (90 s audio). If it ever needs to be cheaper: the few-shot prefix is most of
    those tokens and is identical every time, so its KV cache could be built once at warmup.

Sixteen cases is still a tuning set, not a validation set, and nine of them drove the
changes. Treat the verdict as a signal, never as a verdict on a learner: the application
tier annotates results with it and does not withhold them (see
app/services/speech_assessment_service.py, and the note there on why the zeroing was
removed).

LOW-PROFICIENCY GUARD. The failure that matters here is not a missed off-topic answer, it
is an A1 learner whose halting, error-filled but perfectly on-topic answer gets called
off-topic. That would punish exactly the users the app exists for. Four things guard
against it: the system prompt says so in as many words, few-shot #2 is a barely-grammatical
answer labelled `good`, few-shot #3 is an ASR-mangled one labelled `good`, and `off_topic`
is demoted to `partial` unless the probability clears
DTA_RELEVANCE_OFF_TOPIC_MIN_CONFIDENCE.

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

from .prompt import normalise_text

# --- knobs (runtime only; none of this can move a score) --------------------------------
ENABLED = os.environ.get("DTA_RELEVANCE_CHECK", "1") == "1"
# TWO THRESHOLDS, one per verdict, both read off the SAME three probabilities so they can
# be compared directly:
#
#     p(bad)  >= OFF_TOPIC_MIN_CONFIDENCE  -> off_topic
#     p(good) >= ON_TOPIC_MIN_CONFIDENCE   -> on_topic
#     otherwise                            -> partial
#
# `partial` is what is left over, which is the point: it is the cheap verdict, so it absorbs
# every case neither of the other two is confident enough to claim. Checking `off_topic`
# first means a transcript with concentrated mass on `bad` is called off-topic even if it
# would also clear the on-topic bar; nothing here has ever come close to both.
#
# The two verdicts cost the learner very different amounts, which is why they get separate
# bars rather than an argmax. `off_topic` tells someone who recorded an honest attempt that
# they answered the wrong question -- and measured on real recordings, this judge is usually
# wrong when it says that. Every attempt to make it better at spotting a genuinely wrong
# subject made it more suspicious of ASR-mangled beginner Finnish, at roughly one real answer
# newly warned per control gained. `partial` is a "remember to answer the question" tip
# beside a result that is still shown in full; being wrong costs a needless nudge.
#
# Both values are measured, not chosen -- eval_relevance.py dumps the probabilities for the
# whole tuning set and these are read off that distribution.
#
# 0.70 is deliberately well clear of the real-answer population rather than just past it.
# Genuine learner answers reach p(bad)=0.436 at worst (recording 30, the most ASR-mangled of
# the nine), so this leaves 0.26 of margin. A tighter 0.50 also flags nothing in the tuning
# set and catches one more control -- but it leaves only 0.06 of margin, and the set is nine
# recordings by two speakers. The wide bar is the one likelier to survive a speaker we have
# not heard yet, and the cost of being wrong here is telling someone who genuinely tried
# that they answered the wrong question.
#
# What clears 0.70 in practice: silence hallucinations (0.76-0.94) and answers given in
# another language (0.78). A fluent wrong-subject answer sits at 0.54 and therefore comes
# back `partial` -- still warned, not silently accepted, but not accused.
OFF_TOPIC_MIN_CONFIDENCE = float(
    os.environ.get("DTA_RELEVANCE_OFF_TOPIC_MIN_CONFIDENCE", "0.70"))
# The middle band, 0.40-0.70 on p(bad) without p(good) clearing this bar, is `partial`.
ON_TOPIC_MIN_CONFIDENCE = float(
    os.environ.get("DTA_RELEVANCE_ON_TOPIC_MIN_CONFIDENCE", "0.40"))
# 90 s of speech is ~200 words; the cap only bites on a runaway ASR repetition loop.
MAX_ANSWER_CHARS = int(os.environ.get("DTA_RELEVANCE_MAX_ANSWER_CHARS", "2000"))

VERSION = "dta-relevance-v3"

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

# POINT OF DIVERGENCE FROM THE SCORER. The scorer must send `<TASK task_id=04_h
# task_name=dta-task4_a>\nQ1: ...\nA1: ...` byte for byte -- that is a training-parity
# requirement (prompt.py). The judge has no such constraint: it is zero-shot on base
# weights, and it was inheriting two problems from that template.
#
#   * `task_id=04_h task_name=dta-task4_a` are internal DTA codes. The base model has never
#     seen them and they carry no meaning for a relevance decision -- they are noise in
#     every single example. The research original used a generic `<TASK part=P1>`.
#   * `A1:` meant "Answer 1" in the SANDI format it came from. Here the system prompt also
#     says "candidates are learners at CEFR A1-B1", so the same two characters denote both
#     the answer marker and the lowest CEFR level, in one prompt.
#
# Spelling the two fields out removes both. The scorer's template is untouched.
_JUDGE_BLOCK = "<TASK>\nQUESTION: {question}\nANSWER: {answer}\n\nRelevance:"

# Qwen3's chat template injects an empty reasoning block at the generation position even
# with enable_thinking=False, and it STRIPS reasoning blocks out of assistant history. So
# every demonstration ends `<|im_start|>assistant\ngood<|im_end|>` while the position we
# actually read the label logit from ends `<|im_start|>assistant\n<think>\n\n</think>\n\n`
# -- formatted unlike all nine examples, at the one place it matters. Putting the block
# into the few-shot content does not help (the template removes it again), so it is cut
# from the generation prompt instead, which makes the read position identical to the
# demonstrations.
_THINK_SUFFIX = "<think>\n\n</think>\n\n"


def judge_block(question: str, answer: str) -> str:
    """The task/answer block as the JUDGE sees it. Not the scorer's format."""

    return _JUDGE_BLOCK.format(question=normalise_text(question),
                               answer=normalise_text(answer))


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
    # "verbatim ASR" and the intention-inference sentence are the app owner's wording: the
    # judge kept reading transcription damage as a wrong topic, so the prompt now says up
    # front that damage is expected and that the job is to work out what the speaker meant.
    "You are an examiner for a spoken language exam. You are given the exam TASK (the "
    "question / prompt) and the candidate's spoken answer, transcribed by verbatim ASR "
    "(which means there are typos, grammar errors and mispronunciation errors in the "
    "text). Judge ONLY how well the answer ADDRESSES the task — its topical relevance and "
    "task fulfilment. Do NOT judge grammar, vocabulary, pronunciation or fluency. You "
    "should try your best to infer the speaker's intention from the ASR text. Reply with "
    "exactly one word:\n"
    # `average` no longer says "incomplete". It used to, while the paragraph below told the
    # model that incomplete answers are good -- the prompt argued with itself, and measured
    # on real recordings the "incomplete" reading won: fluent A2+ answers that fully
    # addressed their task came back `average` at 0.46-0.86.
    # `bad` is CASA's wording, unchanged and deliberately broad. Narrowing it to "about
    # something else entirely" was measured at 6/7 -> 4/7 detection, because genuine
    # off-topic answers fell through into `average`.
    "  good = the answer addresses the question / task and stays on topic;\n"
    "  average = the answer starts on the topic of the task but then drifts onto a "
    "different subject;\n"
    "  bad = does not address the question, is off-topic, or is non-responsive.\n"
    # --- what the exam actually is --------------------------------------------------------
    # Nothing used to tell the judge how long an answer is supposed to be, so "is this
    # enough?" was left to its imagination -- and its imagination was calibrated on written
    # English essays, not on 30 seconds of beginner Finnish.
    "\nThe exam is short spoken Finnish. A reaction task gives the candidate 30 seconds; a "
    "monologue or picture task gives one minute. Answers are therefore SHORT -- a handful "
    "of sentences at most, often less. That is the format working as intended, not the "
    "candidate failing to answer.\n"
    # --- what the transcripts actually look like -------------------------------------------
    # Verified against every transcript in the production database: all lowercase, no
    # punctuation at all, hesitation written as repeated words.
    "\nThe transcript has no capital letters and no punctuation, and it never will. "
    "Hesitation appears as repeated words, not as pauses you can see. Do not read either "
    "as the candidate being unclear.\n"
    # --- mispronunciation ------------------------------------------------------------------
    # The failure this prevents: a beginner mispronounces one word, the ASR writes down a
    # different real Finnish word, and the sentence stops making sense word by word.
    "\nCandidates are beginners and mispronounce words, so the ASR often writes a DIFFERENT "
    "real Finnish word or a non-word: \"raha\" (money) can come out as \"vaha\" (wax), "
    "\"euroa\" as \"ilva\". Individual words may therefore be wrong or meaningless even "
    "though the candidate said the right thing. Judge the answer as a whole, not word by "
    "word.\n"
    # --- the low-proficiency guard, with the app owner's other-language addition ------------
    "\nThe speaking task is in Finnish and the candidates are learners at CEFR A1–B1. A "
    "short, hesitant, or heavily error-filled answer that is still ABOUT the task is good "
    "or average, never bad: weak language is not a wrong topic. Answer bad only when the "
    "content is about something else, is empty, the candidate answers in English or "
    "another language, or is not a response to this task at all."
    "\n\nIMPORTANT: you are judging TOPIC, not completeness and not effort. If the answer "
    "is about what the task asked about, answer good -- even if it is short, covers only "
    "part of a multi-part task, leaves questions unanswered, or stops early. Answer "
    "average ONLY when the answer starts on the task and then moves to a different "
    "subject. Answer bad ONLY when the answer is about something else entirely, is empty, "
    "or is not a response to this task at all."
)

# (question, answer, label). The task ids are in the comments rather than the tuple: they
# used to be rendered into every example and are now deliberately absent from the block the
# judge sees -- see judge_block() above.
#
# REAL RECORDINGS WHEREVER THEY EXIST. Six of these eight answers are verbatim production
# transcripts, database ids given per example. They were hand-written Finnish until now,
# which was a mistake with 23 real transcripts sitting in the database: invented learner
# Finnish is a guess at how beginners fail, and the real thing does not look like the guess.
#
# The four remaining synthetic examples are marked SYNTHETIC. Each is a failure mode that
# has not happened in production yet, so there is nothing real to copy:
#   * both `average` examples -- no recording has ever drifted off its task mid-answer;
#   * the wrong-subject `bad` -- nobody has answered a different question fluently;
#   * the three-word `good` -- every real short transcript is a hallucination, not a real
#     short answer, so the "brevity is not evasion" case has to be constructed.
# Replace each with a real recording as soon as one exists.
#
# NOT FROM THE EVAL SET. Database ids 28-36 are the labelled tuning/eval set in
# eval_relevance.py and are deliberately NOT used here -- an example that appears in the
# prompt cannot also measure it. Ids 7 and 8 predate that set and are free to use.
#
# FORMAT -- every answer is a real ASR string or written like one: no capital letters, no
# commas, no full stops, no ellipses, hesitation as repeated words. Verified against all 23
# transcripts in the database: not one contains a capital letter or a punctuation mark.
#
# ORDER -- grouped good / average / bad, deliberately. Interleaving was measured twice with
# content held constant: detection fell 6/7 -> 3/7, and a strict good/average/bad cycle fell
# to 2/7 under three different system prompts.
_FEWSHOT = [
    # id 8, task 1 -- good. REAL. Refuses to lend, explains the money is needed for food.
    ("Sinun ystävällä ei ole paljon rahaa, ja hän pyytää sinulta 100 euroa. Mitä sinä "
     "vastaat hänelle?",
     "hei mä ei haluan anna anta sinun sata euroa mä mä täyttyy ostaa ruokaa kaupasa",
     "good"),
    # id 7, task 1 -- good. REAL, and THE MISPRONUNCIATION CASE, not a constructed one:
    # "euroa" came out as "ilva" and the pronouns are mangled, so word by word much of it is
    # meaningless. As a whole it is plainly still about lending money for food shopping.
    # An earlier prompt called this exact recording off_topic at 0.75.
    ("Sinun ystävällä ei ole paljon rahaa, ja hän pyytää sinulta 100 euroa. Mitä sinä "
     "vastaat hänelle?",
     "moi haluan haluan häne sinu sata ilva mun täytti ostaa ruokakaupassa",
     "good"),
    # SYNTHETIC -- good. THREE WORDS, so that brevity is never mistaken for evasion. Every
    # real short transcript in the database is a hallucination rather than a real short
    # answer, so there is nothing to copy. Replace when a real one turns up.
    ("Sinun ystävällä ei ole paljon rahaa, ja hän pyytää sinulta 100 euroa. Mitä sinä "
     "vastaat hänelle?",
     "ei ole rahaa",
     "good"),
    # SYNTHETIC -- average. Opens on the task, then leaves it for a holiday anecdote.
    # No production recording has ever drifted like this.
    ("Sinä et voi tulla tänään kurssille/töihin. Sinä lähetät ääniviestin "
     "opettajalle/pomolle ja kerrot, miksi sinä et voi tulla. Mitä sinä sanot viestissä?",
     "moi opettaja mä olen vähän kipeä tänään ai niin viime viikolla mä kävin tallinnassa "
     "laivalla ja siellä oli tosi kivaa me syötiin ravintolassa ja ostettiin suklaata ja "
     "laivalla oli musiikkia ja tanssia",
     "average"),
    # SYNTHETIC -- bad, and deliberately a HARD one: fluent, plausible, and simply not an
    # answer to the question asked. Non-responsive rather than off-subject, which is the
    # boundary worth teaching -- a single hallucinated word teaches nothing the model does
    # not already know (it flags "puhemies" at 0.97 with no example at all).
    #
    # Two earlier versions of this slot were both wrong, and measurably:
    #   * a weekend-activities answer, which duplicated a control in eval_relevance.py --
    #     the prompt was teaching the test;
    #   * a polite apology to a teacher, which shares its register with recording 34
    #     ("olen tosi pahoillani ... mulla ei ole tarpeeksi rahaa"). With that example in
    #     the prompt, recording 34 -- a perfect answer -- came back off_topic at 0.667.
    #     Removing the apology fixed it. The model was matching politeness, not topic.
    ("Kerro, mitä kaikkea sinä teet normaalisti kotona.",
     "mä menen töihin bussilla joka aamu ja mun työpaikka on keskustassa ja siellä on "
     "kymmenen ihmistä ja me teemme tietokoneella töitä ja lounas on kello kaksitoista",
     "bad"),
    # id 23, task 1 -- bad. REAL, and chosen over the obvious alternative. The database is
    # full of "puhemies" (what near-silence transcribes to here, six of the first fourteen
    # recordings), but that case is trivial: the model already flags it at 0.93 with no
    # example at all, so an example spends 30 tokens teaching what is already known. This
    # one is harder and more useful -- coherent, plausible Finnish that simply is not a
    # response to the question. Hallucinations are measured in eval_relevance.py instead;
    # easy cases belong in the eval, hard cases belong in the prompt.
    ("Sinun ystävällä ei ole paljon rahaa, ja hän pyytää sinulta 100 euroa. Mitä sinä "
     "vastaat hänelle?",
     "tää on pienempää",
     "bad"),
    # task 5 -- bad. REAL, captured during client testing (quoted in the frontend's
    # TO_BACKEND.md): the candidate answered in English, and ASR_LANGUAGE is pinned to "fi"
    # (asr.py), so the decoder rendered it phonetically as Finnish-looking word salad with
    # occasional real Finnish surfacing. This is the example behind "answers in English or
    # another language" in the system prompt.
    ("Mitä sinä normaalisti ostat kaupasta? Mitä sinä et normaalisti osta kaupasta? "
     "Milloin sinä normaalisti käyt kaupassa?",
     "so i just like my red and butter and then some manana just we like to eat manana and "
     "it really cheap as well mm and rice obviously just we are asians what i dont buy at "
     "the store a washington food cream cream cheese aa we do drink a bit of meal and a bit "
     "of acid when do u normaly go shopping i uselly go shopping at the wikinaa during the "
     "afton kun i have alot of free time at wikinaa me go almost eivver wik tos me niitä "
     "myyä food for the family",
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
        for question, answer, label in _FEWSHOT:
            messages.append(
                {"role": "user", "content": f"{judge_block(question, answer)}"})
            messages.append({"role": "assistant", "content": label})
        return messages

    def _render(self, task, transcript: str) -> str:
        block = judge_block(task_question(task),
                            normalise_text(transcript)[:MAX_ANSWER_CHARS])
        messages = self._prefix + [{"role": "user", "content": block}]
        try:
            rendered = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            # Older template without the thinking switch: the label is still the next token.
            rendered = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True)
        # See _THINK_SUFFIX: make the position we read match the demonstrations.
        if rendered.endswith(_THINK_SUFFIX):
            rendered = rendered[: -len(_THINK_SUFFIX)]
        return rendered

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
        # Not an argmax. The three labels are ordered by what they cost the learner, so the
        # verdict is a pair of thresholds walked from the loudest down -- see the note on
        # OFF_TOPIC_MIN_CONFIDENCE. An argmax would let `bad` win on 0.4 against a split
        # `good`/`average`, which is exactly the case this judge gets wrong most often.
        if probs["bad"] >= OFF_TOPIC_MIN_CONFIDENCE:
            relevance, confidence = "off_topic", probs["bad"]
        elif probs["good"] >= ON_TOPIC_MIN_CONFIDENCE:
            relevance, confidence = "on_topic", probs["good"]
        else:
            # Neither bar cleared. `confidence` here is the mass NOT on "answers the task",
            # which is what the verdict is actually asserting.
            relevance, confidence = "partial", probs["average"] + probs["bad"]

        return {"relevance": relevance, "confidence": round(float(confidence), 3),
                "reason": _REASONS[relevance], "judge": VERSION}
