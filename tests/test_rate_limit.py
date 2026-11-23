# pylint: disable=redefined-outer-name

"""Rate limits (security finding F4). Off in tests by default; switched on here."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import AppEnv, _build_settings
from app.main import app
from app.utils import rate_limit
from app.utils.rate_limit import RateLimit, RateLimiter


class FakeClock:  # pylint: disable=too-few-public-methods
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


RULE = RateLimit("test", limit=3, window_seconds=60)


# --- the limiter itself ---------------------------------------------------------------


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter(clock=FakeClock())

    assert [limiter.hit(RULE, "a") for _ in range(3)] == [None, None, None]
    assert limiter.hit(RULE, "a") == pytest.approx(60)


def test_refusal_reports_the_time_left_in_the_window():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit(RULE, "a")

    clock.now += 45

    assert limiter.hit(RULE, "a") == pytest.approx(15)


def test_window_resets_after_it_ends():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(4):
        limiter.hit(RULE, "a")

    clock.now += 60

    assert limiter.hit(RULE, "a") is None


def test_refused_requests_do_not_extend_the_window():
    """A client hammering while refused must still get back in when the window ends."""

    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit(RULE, "a")
    for _ in range(100):
        clock.now += 0.5
        limiter.hit(RULE, "a")

    clock.now = 1000.0 + 60

    assert limiter.hit(RULE, "a") is None


def test_keys_and_rules_are_counted_separately():
    limiter = RateLimiter(clock=FakeClock())
    other_rule = RateLimit("other", limit=3, window_seconds=60)
    for _ in range(3):
        limiter.hit(RULE, "a")

    assert limiter.hit(RULE, "a") is not None
    assert limiter.hit(RULE, "b") is None
    assert limiter.hit(other_rule, "a") is None


def test_disabled_limiter_allows_everything():
    limiter = RateLimiter(enabled=False, clock=FakeClock())

    assert all(limiter.hit(RULE, "a") is None for _ in range(100))


def test_expired_windows_are_swept_so_memory_stays_bounded():
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    for i in range(RateLimiter._SWEEP_EVERY - 1):  # pylint: disable=protected-access
        limiter.hit(RULE, f"key-{i}")

    clock.now += 61
    limiter.hit(RULE, "trigger-sweep")

    assert len(limiter._windows) <= 1  # pylint: disable=protected-access


# --- configuration ----------------------------------------------------------------------


def test_rate_limiting_is_on_by_default_outside_tests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", AppEnv.DEVELOPMENT.value)
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)

    assert _build_settings().rate_limit_enabled is True


def test_rate_limiting_can_be_switched_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", AppEnv.DEVELOPMENT.value)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    assert _build_settings().rate_limit_enabled is False


# --- endpoints --------------------------------------------------------------------------


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch):
    """Switch the app's limiter on with empty counters, and off again afterwards."""

    rate_limit.LIMITER.reset()
    monkeypatch.setattr(rate_limit.LIMITER, "enabled", True)
    yield
    rate_limit.LIMITER.reset()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _onboarding_form() -> dict[str, str]:
    return {
        "guid": str(uuid4()),
        "consent_accepted": "true",
        "consent_timestamp": "2026-01-01T00:00:00Z",
        "finnish_self_assessment": "A1",
    }


def _assert_rate_limited(response) -> None:
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["type"] == "RATE_LIMITED"
    assert detail["retry_after_seconds"] >= 1
    assert response.headers["Retry-After"] == str(detail["retry_after_seconds"])


def test_onboarding_is_limited_per_ip(client: TestClient, limited):  # pylint: disable=unused-argument
    limit = rate_limit.ONBOARDING_PER_IP.limit
    for _ in range(limit):
        assert client.post("/onboarding", data=_onboarding_form()).status_code == 201

    response = client.post("/onboarding", data=_onboarding_form())

    _assert_rate_limited(response)


def test_level_changes_are_limited_per_user(client: TestClient, limited):  # pylint: disable=unused-argument
    first, second = _onboarding_form(), _onboarding_form()
    for form in (first, second):
        assert client.post("/onboarding", data=form).status_code == 201

    for _ in range(rate_limit.LEVEL_PER_USER.limit):
        response = client.patch(
            "/users/level", data={"guid": first["guid"], "cefr_level": "A2"})
        assert response.status_code == 200

    _assert_rate_limited(client.patch(
        "/users/level", data={"guid": first["guid"], "cefr_level": "A2"}))
    # Another learner behind the same address is unaffected.
    assert client.patch(
        "/users/level", data={"guid": second["guid"], "cefr_level": "A2"}).status_code == 200


def test_unknown_guids_do_not_create_per_user_counters(client: TestClient, limited):  # pylint: disable=unused-argument
    """Per-user limits apply after the user is found, so made-up guids cost no memory."""

    for _ in range(50):
        response = client.patch(
            "/users/level", data={"guid": str(uuid4()), "cefr_level": "A2"})
        assert response.status_code == 404

    keys = rate_limit.LIMITER._windows  # pylint: disable=protected-access
    assert not [slot for slot in keys if slot[0] == rate_limit.LEVEL_PER_USER.name]


def test_limits_are_documented_on_every_route():
    schema = app.openapi()
    for path in ("/onboarding", "/feedback", "/speech/assess", "/users/level", "/events"):
        operations = schema["paths"][path]
        for operation in operations.values():
            assert "429" in operation["responses"], path
