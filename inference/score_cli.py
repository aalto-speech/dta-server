"""Score one recording without starting the server. Useful for a first check on a new box.

    python score_cli.py --audio answer.wav --task_id 03_m
    python score_cli.py --audio answer.wav --task_id dta-task2_a --json
"""
import argparse
import json
import sys

from dta_scorer.config import DEVICE, check_weights
from dta_scorer.pipeline import ScoringPipeline
from dta_scorer.tasks import UnknownTask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--task_id", required=True, help="e.g. 03_m, or the name dta-task2_a")
    ap.add_argument("--transcript", default=None,
                    help="skip ASR and score this text (debugging only)")
    ap.add_argument("--device", default=DEVICE)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list_tasks", action="store_true")
    a = ap.parse_args()

    problems = check_weights()
    if problems:
        print("not ready to run:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("\nstage them on the research machine:\n"
              "  python scripts/dta_production/stage_weights.py", file=sys.stderr)
        return 2

    pipe = ScoringPipeline(device=a.device)
    if a.list_tasks:
        for t in pipe.tasks.dta_tasks():
            print(f"{t.task_id:<9}{t.task_name:<16}{t.prompt_fi[:70]}...")
        return 0

    try:
        out = pipe.score_file(a.audio, a.task_id, transcript=a.transcript)
    except UnknownTask as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    c = out["cefr"]
    print(f"\ntask        {out['task']['task_id']}  ({out['task']['task_name']})")
    print(f"audio       {out['audio']['duration_sec']}s"
          + ("  TRUNCATED to 120s" if out["audio"]["truncated"] else ""))
    print(f"transcript  {out['transcript']}\n")
    print(f"CEFR        {c['score']:.2f}  {c['label_fine']:<4} (calibrated)"
          + ("   [CLIPPED at calibration bound]" if c["clipped_to_calibration_range"] else ""))
    print(f"            {c['score_uncalibrated']:.2f}       uncalibrated model output")
    print("\ndimensions (raw, uncalibrated)")
    for d, v in out["dimensions"].items():
        print(f"  {d:<15}{v['score']:.2f}  {v['label_fine']}")
    print(f"\n{out['timings_ms']['total']:.0f} ms total "
          f"(asr {out['timings_ms']['asr']:.0f}, score {out['timings_ms']['scoring']:.0f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
