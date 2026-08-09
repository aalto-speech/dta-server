"""CEFR banding for PRODUCTION OUTPUT. This file owns the rule — never derive labels elsewhere.

    A1   [0, 2)          A2   [2, 2.5)          A2+  [2.5, 3)          B1   [3, ...)

Four labels. That is the whole set. There is no `<A1`, no `A1+`, no `B1+`, and nothing
above B1, on the wire or in the app.

THIS RULE IS TIED TO THE CURRENT MODEL AND IS EXPECTED TO CHANGE.
It is not a statement about CEFR, and it is not the model's own banding. It is what this
version of production is willing to show a learner, given what this version of the model
can actually resolve. When a future checkpoint predicts the finer scale reliably, this rule
changes with it — bands get added back here, and `docs/FRONTEND.md` and the client change
in the same release. Treat the four labels as a property of the deployment, not a constant.

WHY IT DIFFERS FROM THE MODEL PACKAGE. The scorer and its isotonic calibrator produce
whatever they produce, on the full `<A1`..C2 scale, and the inference container labels that
output with the research convention (floor for coarse, round to the nearest half step for
fine). That is correct for the research artifact and it stays untouched. It is not what
production reports, because:

  * This model does not predict A1+ or B1+ reliably. A1+ falls out of a narrow band of
    calibration knots, and B1+ is just the value the calibrator clips to. Publishing them
    dresses noise up as a level.
  * `<A1` is not something this app tells anyone. A learner who recorded an answer is at
    least A1 as far as the product is concerned.

So the app tier re-derives every label from the numeric score and ignores the labels the
inference service sends. The scores themselves are never altered — they remain the
calibrated model output, and anyone who wants the model's own banding can compute it from
the number.

Boundaries are inclusive at the bottom, exclusive at the top, exactly as written above:
2.0 is A2, 2.5 is A2+, 3.0 is B1. **2.41 is A2, not A2+** — this rule floors into a band,
it does not round to the nearest one.
"""

# (upper bound, label), walked in order. The top band is open-ended: this model cannot
# resolve above B1, and a raw dimension score of 5.0 is still just "B1" to production.
_FINE_BANDS = ((2.0, "A1"), (2.5, "A2"), (3.0, "A2+"))

# The same rule without the half step, for the coarse label: A1 [0,2), A2 [2,3), B1 [3,...).
_COARSE_BANDS = ((2.0, "A1"), (3.0, "A2"))

_TOP = "B1"


def _band(score: float, bands: tuple[tuple[float, str], ...]) -> str:
    for upper, name in bands:
        if float(score) < upper:
            return name
    return _TOP


def label_fine(score: float) -> str:
    """Production CEFR label including the plus level: A1, A2, A2+ or B1."""

    return _band(score, _FINE_BANDS)


def label(score: float) -> str:
    """Production CEFR label without the plus level: A1, A2 or B1."""

    return _band(score, _COARSE_BANDS)


def labels(score: float) -> dict[str, str]:
    """Both labels for one score, in the shape the API returns per dimension."""

    return {"label": label(score), "label_fine": label_fine(score)}
