import pytest

from app.utils.ranking import _PERCENT_RUNGS, _RANK_RUNGS, build_display


@pytest.mark.parametrize(
    "rank, cohort_size, expected_percent",
    [
        (1, 100, 1),      # exactly on a rung stays on it
        (1, 1000, 1),     # finer than the finest rung -> still the finest rung
        (5, 100, 5),
        (6, 100, 10),     # 6% rounds UP to 10, never down to 5
        (11, 100, 25),
        (26, 100, 50),
        (50, 100, 50),    # the halfway mark is still displayed
    ],
)
def test_percent_rungs_round_up(rank: int, cohort_size: int, expected_percent: int):
    """A displayed percentage is always a true ceiling on the learner's position."""

    assert build_display(rank, cohort_size)["top_percent"] == expected_percent


@pytest.mark.parametrize("rank, expected_rank", [
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (9, 9), (10, 10),
    (11, 25), (25, 25), (26, 50), (50, 50), (51, 100), (100, 100),
])
def test_rank_rungs_round_up(rank: int, expected_rank: int):
    """Places 1-10 are exact; past them, rank buckets round up: #46 is "top 50"."""

    assert build_display(rank, 1000)["top_rank"] == expected_rank


def test_a_place_up_to_ten_is_always_exact():
    """The app renders any `top_rank` up to 10 as "#N", so it must be the real place.

    A rounded-up 5 for a learner in 4th would be shown as "#5": a place they do not hold.
    """

    for cohort_size in range(1, 201):
        for rank in range(1, cohort_size + 1):
            top_rank = build_display(rank, cohort_size)["top_rank"]

            if top_rank is not None and top_rank <= 10:
                assert top_rank == rank


def test_rank_is_dropped_past_the_last_rung():
    """Past 100 the rank number carries no information, so only the percentage remains."""

    display = build_display(101, 1000)

    assert display["top_rank"] is None
    assert display["top_percent"] == 25


def test_nothing_is_displayed_below_the_halfway_mark():
    """A learner in the bottom half sees their score and band, and no position."""

    assert build_display(51, 100) == {"top_percent": None, "top_rank": None}


def test_bottom_half_hides_the_rank_even_when_the_number_is_small():
    """A small cohort must not leak a flattering rank to a bottom-half learner.

    #6 of 10 is bottom-half, yet #6 is a top-ten place. The cutoff is evaluated first
    precisely so that it is not shown.
    """

    assert build_display(6, 10) == {"top_percent": None, "top_rank": None}


def test_last_place_displays_nothing():
    assert build_display(53, 53) == {"top_percent": None, "top_rank": None}


def test_top_of_a_small_cohort_does_not_claim_more_than_the_cohort_supports():
    """Rank 1 of 53 is top 1.9%, so the honest bucket is 5% -- not 1%."""

    display = build_display(1, 53)

    assert display["top_percent"] == 5
    assert display["top_rank"] == 1


def test_degenerate_inputs_display_nothing():
    assert build_display(1, 0) == {"top_percent": None, "top_rank": None}
    assert build_display(0, 10) == {"top_percent": None, "top_rank": None}


def test_every_displayed_bucket_is_a_true_statement():
    """Exhaustive check of the only property that matters: no bucket ever overstates.

    `top_percent` must be >= the learner's real percentage, and `top_rank` >= their
    real rank. A bucket that is smaller than the truth is a false claim shown to a
    learner, which is the one failure mode this module exists to prevent.
    """

    for cohort_size in range(1, 201):
        for rank in range(1, cohort_size + 1):
            display = build_display(rank, cohort_size)
            real_percent = (rank / cohort_size) * 100

            if display["top_percent"] is not None:
                assert display["top_percent"] >= real_percent
            if display["top_rank"] is not None:
                assert display["top_rank"] >= rank


def test_ladders_are_ascending_and_unique():
    """Guards against a careless edit reordering a rung, which would break rounding."""

    assert list(_PERCENT_RUNGS) == sorted(set(_PERCENT_RUNGS))
    assert list(_RANK_RUNGS) == sorted(set(_RANK_RUNGS))
