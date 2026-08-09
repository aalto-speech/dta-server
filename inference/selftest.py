"""Post-deployment self-test. Runs on the SERVER, needs no research data.

    python selftest.py

Checks the things that actually break a deployment: weights present and loadable, the
architecture matching the checkpoint, calibration wired in, task catalogue resolving, and a
real end-to-end forward pass producing a well-formed score.

It CANNOT check that the numbers match the research run — that requires the held-out test
set and its labels, which do not ship. That comparison is
`scripts/dta_production/verify_parity.py` in the research repo and must be run there.
"""
import sys
import time

import numpy as np

from dta_scorer.calibration import IsotonicCalibrator
from dta_scorer.config import DEVICE, SAMPLE_RATE, check_weights, load_model_card
from dta_scorer.tasks import TaskCatalogue, UnknownTask

DIMS = ["fluency", "pronunciation", "range", "accuracy"]


def main() -> int:
    fails = []

    def check(name, ok, detail=""):
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not ok:
            fails.append(name)

    # ---- assets, no model load ---------------------------------------------------------
    problems = check_weights()
    check("weights and assets present", not problems,
          "" if not problems else "; ".join(problems))
    if problems:
        print("\nrun stage_weights.py on the research machine and copy weights/ across")
        return 1

    card = load_model_card()
    check("model card", card["checkpoint"].endswith("ttsfluall3_s2022"), card["checkpoint"])

    cal = IsotonicCalibrator.load()
    lo, hi = cal.output_range
    mono = all(cal(a) <= cal(b) + 1e-9 for a, b in zip(np.arange(0, 6, 0.05),
                                                       np.arange(0.05, 6.05, 0.05)))
    check("calibrator is monotone and bounded", mono and hi <= 3.5001,
          f"range [{lo:.2f}, {hi:.2f}], {len(cal.x)} knots")
    check("calibrator clips below/above", cal(-5.0) == lo and cal(99.0) == hi)

    cat = TaskCatalogue.load()
    check("task catalogue", len(cat.dta_tasks()) == 6, f"{len(cat)} tasks, 6 DTA")
    try:
        cat.get("no_such_task")
        check("unknown task raises", False)
    except UnknownTask:
        check("unknown task raises", True)
    t = cat.get("03_m")
    check("task resolves by id and name",
          cat.get("dta-task2_a").task_id == "03_m" and t.model_task_id == 23,
          f"03_m -> {t.task_name}, embedding row {t.model_task_id}")

    # ---- model load + forward ----------------------------------------------------------
    print(f"\nloading model on {DEVICE} (first load is slow: ~9 GB) ...", flush=True)
    t0 = time.perf_counter()
    from dta_scorer.pipeline import ScoringPipeline
    pipe = ScoringPipeline(device=DEVICE)
    check("model loads with a strict state-dict match", True,
          f"{time.perf_counter()-t0:.0f}s")
    check("frozen OLS matches the model card",
          np.allclose(pipe.scorer.ols_coef, card["frozen_ols"]["coef"], atol=1e-6),
          str([round(c, 4) for c in pipe.scorer.ols_coef]))

    # A synthetic waveform is meaningless speech; the point is that the pipeline runs end to
    # end and returns a well-formed, in-range result -- not that the score is sensible.
    rng = np.random.default_rng(0)
    wave = (0.02 * rng.standard_normal(8 * SAMPLE_RATE)).astype(np.float32)
    import soundfile as sf, tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, wave, SAMPLE_RATE)
    try:
        t1 = time.perf_counter()
        out = pipe.score_file(tmp.name, "03_m")
        elapsed = time.perf_counter() - t1
    finally:
        os.unlink(tmp.name)

    c = out["cefr"]
    check("end-to-end score returned", isinstance(c["score"], float), f"{elapsed:.1f}s")
    check("calibrated CEFR inside the reportable range", lo - 1e-6 <= c["score"] <= hi + 1e-6,
          f"{c['score']:.3f} in [{lo:.2f}, {hi:.2f}]")
    # The response rounds both score and score_uncalibrated to 3 decimals, so the
    # recomputation can differ by up to ~3e-3 (5e-4 output rounding + max knot
    # slope x 5e-4 input rounding). An *uncalibrated* score would differ by ~0.4,
    # so 5e-3 still separates the two cases cleanly. (1e-6 failed spuriously at
    # the calibration floor: served round(1.13875, 3)=1.139 vs recomputed 1.13875.)
    check("calibration actually applied",
          abs(c["score"] - cal(c["score_uncalibrated"])) < 5e-3,
          f"raw {c['score_uncalibrated']:.3f} -> {c['score']:.3f}")
    check("all four dimensions returned",
          all(d in out["dimensions"] for d in DIMS),
          ", ".join(f"{d}={out['dimensions'][d]['score']:.2f}" for d in DIMS))
    check("dims are served raw (documented inconsistency)",
          all(out["dimensions"][d]["calibration"] is None for d in DIMS))
    check("transcript produced by ASR", out["transcript_source"].startswith("asr:"),
          f"{len(out['transcript'])} chars from 8 s of noise")

    # ---- content relevance -------------------------------------------------------------
    # Skipped, not failed, when DTA_RELEVANCE_CHECK=0: serving without the content channel
    # is a supported configuration.
    if pipe.judge is None:
        print("skip  relevance judge (DTA_RELEVANCE_CHECK=0)")
    else:
        check("relevance block present on every result",
              isinstance(out["content"], dict)
              and out["content"]["relevance"] in ("on_topic", "partial", "off_topic"),
              f"{out['content']} on 8 s of noise")

        # Supplied transcripts, so this tests the judge and not the ASR. The task asks what
        # the candidate normally does at home; the second answer is about a nuclear reactor,
        # which is the research repo's own attack shape.
        home = cat.get("04_h")
        t2 = time.perf_counter()
        on = pipe.judge.judge(home, "Kotona minä teen ruokaa ja siivoan melkein joka päivä. "
                                    "Illalla katson televisiota ja luen kirjaa.")
        off = pipe.judge.judge(home, "Ydinreaktori tuottaa sähköä ja siinä käytetään "
                                     "uraania polttoaineena. Reaktorin jäähdytys on "
                                     "tärkeää turvallisuuden takia.")
        judge_ms = (time.perf_counter() - t2) * 500  # two calls
        check("judge separates on-topic from off-topic",
              on["relevance"] == "on_topic" and off["relevance"] == "off_topic",
              f"on={on['relevance']} {on['confidence']:.2f} / "
              f"off={off['relevance']} {off['confidence']:.2f}, ~{judge_ms:.0f} ms each")
        check("silence is not sent to the judge",
              pipe.judge.judge(home, "  ...  ")["relevance"] == "off_topic")

    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}")
    if not fails:
        print("Deployment is functional. Numerical agreement with the research run is a "
              "separate check:\n  scripts/dta_production/verify_parity.py (research repo, GPU)")

    # Deliberately AFTER the pass/fail line and not counted as a failure: these are
    # application-side decisions this package cannot make or verify. They are printed every
    # run because both fail silently in production -- see ACTION_REQUIRED.md.
    _outstanding_actions()
    return 0 if not fails else 1


def _outstanding_actions() -> None:
    import json
    from dta_scorer.config import TASK_ID_MAP_JSON

    # Only genuinely-blocking items belong here. A banner that fires every run for something
    # nobody needs to act on trains people to skip banners, which costs more than it saves --
    # so the score-scale note lives in ACTION_REQUIRED.md and is NOT printed: 5 is C1 and the
    # calibrated score is capped at 3.50, so that bound cannot be reached.
    notes = []
    try:
        m = json.loads(TASK_ID_MAP_JSON.read_text())
        if not m.get("confirmed_by_deployer", False):
            notes.append(
                "TASK-ID MAP IS UNCONFIRMED. assets/task_id_map.json maps the app's integer\n"
                "     task_id onto this package's tasks. A wrong map scores every recording\n"
                "     plausibly and wrongly, with no error anywhere. Verify against the app,\n"
                "     then set \"confirmed_by_deployer\": true to silence this.")
    except (OSError, ValueError):
        notes.append("assets/task_id_map.json missing or unreadable — integer task ids "
                     "will be rejected.")

    if not notes:
        return
    print("\n" + "=" * 72)
    print("OUTSTANDING — fails SILENTLY if skipped (ACTION_REQUIRED.md)")
    print("=" * 72)
    for i, n in enumerate(notes, 1):
        print(f"  {i}. {n}")


if __name__ == "__main__":
    raise SystemExit(main())
