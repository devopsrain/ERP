"""Tests for the with_retries helper in app.daily_correlation.

No network, no real sleeps: the callable is a fake and the sleep function is
injected so the backoff schedule can be asserted without waiting.
"""
import pytest

import app.daily_correlation as dc


def _flaky(fail_times: int, result="ok"):
    """Callable that raises `fail_times` times, then returns `result`."""
    state = {"calls": 0}

    def call():
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise ValueError(f"boom #{state['calls']}")
        return result

    call.state = state
    return call


def test_success_on_first_attempt_never_sleeps():
    call = _flaky(0, result=42)
    out = dc.with_retries(call, what="test",
                          sleep=lambda s: pytest.fail("must not sleep on success"))
    assert out == 42
    assert call.state["calls"] == 1


def test_fail_twice_then_succeed_uses_backoff_schedule():
    sleeps = []
    call = _flaky(2)
    out = dc.with_retries(call, what="test", sleep=sleeps.append)
    assert out == "ok"
    assert call.state["calls"] == 3
    # two backoffs: base 2s then 5s, each with up to +50% jitter
    assert len(sleeps) == 2
    assert 2.0 <= sleeps[0] <= 3.0
    assert 5.0 <= sleeps[1] <= 7.5


def test_raises_last_error_after_final_attempt():
    sleeps = []
    call = _flaky(99)  # never succeeds
    with pytest.raises(ValueError, match="boom #3"):
        dc.with_retries(call, what="test", sleep=sleeps.append)
    assert call.state["calls"] == 3          # exactly 3 attempts
    assert len(sleeps) == 2                  # no sleep after the last attempt


def test_jitter_is_bounded_and_deterministic_with_injected_rng():
    sleeps = []
    call = _flaky(2)
    dc.with_retries(call, what="test", sleep=sleeps.append, rng=lambda: 1.0)
    assert sleeps == [2.0 * 1.5, 5.0 * 1.5]  # rng=1.0 -> full +50% jitter

    sleeps.clear()
    call = _flaky(2)
    dc.with_retries(call, what="test", sleep=sleeps.append, rng=lambda: 0.0)
    assert sleeps == [2.0, 5.0]              # rng=0.0 -> bare base delays


def test_extra_attempts_reuse_last_delay():
    sleeps = []
    call = _flaky(4)
    out = dc.with_retries(call, what="test", attempts=5,
                          sleep=sleeps.append, rng=lambda: 0.0)
    assert out == "ok"
    assert sleeps == [2.0, 5.0, 12.0, 12.0]  # schedule then clamp to last entry
