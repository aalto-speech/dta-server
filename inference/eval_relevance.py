"""Re-measure the content-relevance judge against its tuning set.

The numbers quoted in `dta_scorer/relevance.py` come from this script. It needs the model
weights and a GPU, so CI cannot run it -- run it by hand after any change to the prompt,
the few-shot examples, or the threshold, and update the docstring with what you get.

    podman run --rm --device nvidia.com/gpu=all \
      -v asa-weights:/weights:ro -v $PWD/inference:/src:ro -w /src \
      ghcr.io/aalto-speech/dta-server/inference:latest python eval_relevance.py

THE TUNING SET IS NOT A VALIDATION SET. Nine of these sixteen cases are real production
recordings whose labels come from the app owner, and they are the cases that drove the v2
prompt -- so a good score here is a check that nothing regressed, not evidence the judge
generalises. Add real recordings as they arrive; that is what makes this worth more.

The bar to clear:
  * FLAGGED must be 0. No genuine attempt may come back `off_topic` -- that is the loudest
    thing the app can say to a learner, and on this evidence it is usually wrong when it
    says it. (Until v1.3.0 an `off_topic` verdict also zeroed the five scores, so this line
    used to say "destroyed". The app no longer withholds anything: a false positive now
    costs an undeserved warning rather than a grade.)
  * CAUGHT must not fall below 6/7. If a change fixes a miss by making the judge agreeable,
    it has removed the feature rather than fixed it.
"""
import os
import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from dta_scorer import config
from dta_scorer.relevance import OFF_TOPIC_MIN_CONFIDENCE, VERSION, RelevanceJudge
from dta_scorer.tasks import TaskCatalogue

# Real production recordings, verbatim from the database. Ground truth from the app owner:
# every one was a genuine attempt to answer the task it was given, so `off_topic` is always
# wrong here. Speaker level is the app owner's own estimate.
REAL = [
    (28, 1, "A1", "ei mä ei haluan antaa sinu vähä koska mä mun täyty osta osta ruoka "
                  "miinula ei paljon vähä nyt"),
    (29, 2, "A1", "hei me mä ei menen koulutanan koska minulla on saivas"),
    (30, 3, "A1", "kotona on kasso teevee ja peili tietokone geemi keitoluoka ja nukkuu "
                  "ja suihkuu ja luen uusi netissä"),
    (31, 4, "A1", "kotona puhu puhut me vietnamis toissa puhu elantia ja kaupassa puhu "
                  "elantia tai vähän suomea kasso tv onlantia ja paljon enlantia "
                  "televisio kasso filmi myös mutta ei paljon"),
    (32, 5, "A1", "namali osta banaani koska tai ty haluan syö suon banaani mun tyttö "
                  "haluan paljon jäätelö suklaa vanila ja mansikka mutta hän ei saa suot "
                  "paljon jäätelö osta leipää koska leipä on hyvää ei osta kahvi en juo "
                  "kahvi"),
    (33, 5, "A2+", "mä ylensä käyn kaupan kaupan kaks kertaa viikossa mä ostan usein "
                   "ruoka ja juoma esimerkiksi maito mehua ja lihaa vihanneksia ja "
                   "välimiia joskut ostan myös jaski mun tuttarele en en koskaan ostanut "
                   "kahvia tai teetä koska en juo niitä okei"),
    (34, 1, "A2+", "olen tosi pahoillani se asia haluan lainan lainan sinulle rahaa "
                   "valite valitavasti mulla ei ole tarpeeksi rahaa nyt sain pa mun "
                   "palkan kaksi viikon kuluttua"),
    (35, 2, "A2+", "hei anna olen tosi pahoillani en voi tula kursile tänän mun tyt mun "
                   "tyttö on saira hänellä on kumetta ja nuuha luulen että opiskelen ise "
                   "kotona nähään ylihuomenna"),
    (36, 3, "A2+", "herän yleensä kello seitsemän sitten herätän mun tyttö hän hän päät "
                   "häriä häriämisen jälkeen me syödään aamupala yleensä sen jälkeen "
                   "vietän hänen hänet päiväkotiin sitten pää tule tulen takaisin kotiin "
                   "ja opiskelen suomea verkossa kelo kaksitoista syön lounasta yksin "
                   "kotona koska mun mies on töissä"),
]

# Must still be caught. The first three are what near-silence actually transcribes to --
# they are the common case in production, not a contrived one.
CONTROLS = [
    ("hallucination: puhemies", 1, "puhemies"),
    ("hallucination: Yle credit", 1, "Tekstitys: Yle. Kiitos kun katsoit videon!"),
    ("single word", 1, "päivä"),
    ("wrong subject: weather", 1,
     "tänään on kaunis sää ja aurinko paistaa. minä menen ulos kävelemään puistoon."),
    ("wrong subject: football", 4,
     "viikonloppuna minä pelaan jalkapalloa kavereiden kanssa ja katson elokuvia "
     "ja sunnuntaina nukun pitkään ja syön pizzaa"),
    ("english salad", 5,
     "ai juusuali kou tuu se maaketti ja ai bai som milkki ja bredi ja se on se"),
    ("answers a different DTA task", 3,
     "olen tosi pahoillani en voi lainata sinulle sataa euroa nyt koska minulla ei ole "
     "tarpeeksi rahaa ennen palkkaa"),
]


def main() -> int:
    device = os.environ.get("DTA_DEVICE", "cuda")
    dtype = torch.float16 if device.startswith("cuda") else torch.float32

    print(f"judge {VERSION}, threshold {OFF_TOPIC_MIN_CONFIDENCE}, {device}/{dtype}\n",
          flush=True)
    tokenizer = AutoTokenizer.from_pretrained(str(config.QWEN_DIR))
    model = AutoModelForCausalLM.from_pretrained(str(config.QWEN_DIR), dtype=dtype)
    model.eval().to(device)

    class _Scorer:  # the two attributes RelevanceJudge reads, without loading the scorer
        class model:  # pylint: disable=too-few-public-methods
            qwen = None
        tokenizer = None
        autocast_dtype = None

    _Scorer.model.qwen = model
    _Scorer.tokenizer = tokenizer
    _Scorer.autocast_dtype = dtype

    judge = RelevanceJudge(_Scorer(), device=device)
    catalogue = TaskCatalogue.load()

    print("REAL production recordings -- all genuine attempts:")
    flagged = 0
    for db_id, task_id, level, transcript in REAL:
        verdict = judge.judge(catalogue.get(task_id), transcript)
        bad = verdict["relevance"] == "off_topic"
        flagged += bad
        print(f"  {'FLAGGED' if bad else '       '} id={db_id:<3} task={task_id} "
              f"{level:<3} {verdict['relevance']:<10} {verdict['confidence']}")

    print("\nCONTROLS -- must be caught:")
    caught = 0
    for name, task_id, transcript in CONTROLS:
        verdict = judge.judge(catalogue.get(task_id), transcript)
        hit = verdict["relevance"] == "off_topic"
        caught += hit
        print(f"  {'ok  ' if hit else 'MISS'} {name:<30} {verdict['relevance']:<10} "
              f"{verdict['confidence']}")

    print(f"\nFLAGGED {flagged}/{len(REAL)}   CAUGHT {caught}/{len(CONTROLS)}")
    ok = flagged == 0 and caught >= 6
    print("PASS" if ok else "FAIL -- see the bar in this file's docstring")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
