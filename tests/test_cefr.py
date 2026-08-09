import pytest

from app.utils import cefr


@pytest.mark.parametrize("score,expected", [
    # A1 covers everything below 2.0, including 0 -- production has no band under A1.
    (0.0, "A1"), (0.9, "A1"), (1.0, "A1"), (1.14, "A1"), (1.69, "A1"), (1.99, "A1"),
    # Boundaries are inclusive at the bottom.
    (2.0, "A2"), (2.26, "A2"), (2.41, "A2"), (2.49, "A2"),
    (2.5, "A2+"), (2.9, "A2+"), (2.99, "A2+"),
    (3.0, "B1"), (3.5, "B1"), (6.0, "B1"),
])
def test_label_fine_bands(score: float, expected: str):
    """The production rule: A1 [0,2), A2 [2,2.5), A2+ [2.5,3), B1 [3, ...)."""

    assert cefr.label_fine(score) == expected


@pytest.mark.parametrize("score,expected", [
    (0.0, "A1"), (1.99, "A1"),
    (2.0, "A2"), (2.5, "A2"), (2.99, "A2"),
    (3.0, "B1"), (6.0, "B1"),
])
def test_label_coarse_bands(score: float, expected: str):
    """The same rule without the plus level: A1 [0,2), A2 [2,3), B1 [3, ...)."""

    assert cefr.label(score) == expected


def test_the_label_set_is_exactly_four_values():
    """Guard the whole point of this module.

    `<A1`, `A1+` and `B1+` are what the model package emits and what production must not.
    Sweeping the full 0-6 range in small steps is the cheapest way to catch a future edit
    that reintroduces one of them.
    """

    seen = set()
    step = 0.01
    value = 0.0
    while value <= 6.0:
        seen.add(cefr.label_fine(value))
        seen.add(cefr.label(value))
        value = round(value + step, 2)

    assert seen == {"A1", "A2", "A2+", "B1"}
    assert not any(bad in seen for bad in ("<A1", "A1+", "B1+", "B2", "C1", "C2"))


def test_labels_returns_the_api_shape():
    """`labels()` is what a dimension entry looks like on the wire."""

    assert cefr.labels(2.41) == {"label": "A2", "label_fine": "A2"}
    assert cefr.labels(2.5) == {"label": "A2", "label_fine": "A2+"}
    assert cefr.labels(3.2) == {"label": "B1", "label_fine": "B1"}


def test_floors_into_a_band_rather_than_rounding_to_the_nearest():
    """2.41 is A2. The model package would round it to 2.5 and call it A2+.

    This is the difference that made the client's star tiers contradict the label, so it
    is worth a test of its own rather than only living in the band table above.
    """

    assert cefr.label_fine(2.41) == "A2"
    assert cefr.label_fine(2.26) == "A2"
    assert cefr.label_fine(2.74) == "A2+"
