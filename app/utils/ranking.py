"""Display buckets for cohort comparison.

THE SERVER OWNS THE DISPLAYED POSITION. The app renders whatever bucket arrives here
and never derives one from `rank` or `percentile` itself, for the same reason it does
not derive the CEFR label: these ladders will be revised as cohorts grow, and a revision
has to reach every installed app without a client release.

Two rules govern both ladders:

1. Rungs are spaced by ratio, not by a fixed increment. Perceived difference between
   positions scales with ratio (Weber-Fechner), so #9 -> #10 is a real step while
   #109 -> #110 is not. Fine near the top, coarse further down.
2. Every rung is rounded UP, away from the learner. "Top 5%" is a ceiling claim, so a
   learner who is actually at 3.4% is shown "top 5%" -- true -- and never "top 3%",
   which would not be. The same applies to rank: #46 is shown as "top 50".

Nothing is displayed below the halfway mark. A learner in the bottom half sees their
score and CEFR band and no position at all, which is a criterion-referenced statement
rather than a verdict against their peers. See FUTURE_PLAN.md for why.
"""

# Percent rungs, in "top N%" terms. Reached by ratio, not by even steps.
_PERCENT_RUNGS = (1, 5, 10, 25, 50)

# Rank rungs, in "top N" terms. The podium and "top ten" are categories learners already
# carry, so those rungs are the finest ones. Past 100 a rank number stops carrying
# information -- "#340" reads as "a lot" -- and only the percent bucket is shown.
_RANK_RUNGS = (1, 2, 3, 5, 10, 25, 50, 100)

# Below this, no position is displayed at all.
_DISPLAY_CUTOFF_PERCENT = 50.0


def _round_up_to_rung(value: float, rungs: tuple[int, ...]) -> int | None:
    """Return the smallest rung that is >= value, or None if value exceeds every rung."""

    for rung in rungs:
        if value <= rung:
            return rung

    return None


def build_display(rank: int, cohort_size: int) -> dict[str, int | None]:
    """Bucket a raw rank into the values the app is allowed to display.

    Args:
        rank: The learner's 1-indexed competition rank within their cohort.
        cohort_size: Number of learners in the cohort.

    Returns:
        dict: `top_percent` and `top_rank`, either of which may be None. Both None
            means the app displays no position -- score and CEFR band only.
    """

    if cohort_size <= 0 or rank < 1:
        return {"top_percent": None, "top_rank": None}

    top_percent_raw = (rank / cohort_size) * 100

    if top_percent_raw > _DISPLAY_CUTOFF_PERCENT:
        return {"top_percent": None, "top_rank": None}

    return {
        "top_percent": _round_up_to_rung(top_percent_raw, _PERCENT_RUNGS),
        "top_rank": _round_up_to_rung(rank, _RANK_RUNGS),
    }
