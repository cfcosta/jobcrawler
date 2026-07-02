"""Tests for the §4.2 host-side generation cost backstops.

The RLM's ``max_llm_calls`` is a *soft* cap (a recoverable in-sandbox error the
generated code can catch and loop past). ``MAX_SUBCALLS`` is the host's only
*unconditional* stop: a sub-call ceiling enforced on the ``sub_lm`` the RLM calls
for every ``llm_query``, raising a ``BaseException`` the untrusted crawler code
cannot catch-and-continue past.
"""

from __future__ import annotations

import dspy
import pytest

from jobcrawler.generate.budget import (
    CappedLM,
    SubcallBudget,
    SubcallBudgetExceeded,
)


def test_subcall_kill_is_uncatchable_by_except_exception() -> None:
    # The whole point of the host kill: a generated crawler's `except Exception`
    # around an llm_query must NOT be able to swallow it (§4.2). BaseException does
    # that; a plain Exception would let the catch-and-continue loop survive.
    assert issubclass(SubcallBudgetExceeded, BaseException)
    assert not issubclass(SubcallBudgetExceeded, Exception)


def test_budget_charges_up_to_limit_then_raises() -> None:
    budget = SubcallBudget(2)
    budget.charge()
    budget.charge()
    with pytest.raises(SubcallBudgetExceeded):
        budget.charge()


def test_budget_reset_restores_the_full_allowance() -> None:
    # One sub_lm is reused across a fan-out and a whole batch, so the per-trajectory
    # budget must reset at the start of each generation.
    budget = SubcallBudget(1)
    budget.charge()
    budget.reset()
    budget.charge()  # fresh trajectory — no raise
    assert budget.used == 1


def test_capped_lm_aborts_after_max_subcalls(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub the underlying dspy.LM call so the test never hits the network; CappedLM
    # charges, then delegates to super().__call__ (the stub).
    monkeypatch.setattr(dspy.LM, "__call__", lambda self, *a, **k: ["stub"])
    lm = CappedLM("openrouter/cheap", max_subcalls=2, max_tokens=10)
    assert lm("a") == ["stub"]
    assert lm("b") == ["stub"]
    with pytest.raises(SubcallBudgetExceeded):
        lm("c")


def test_capped_lm_reset_subcalls_starts_a_fresh_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dspy.LM, "__call__", lambda self, *a, **k: ["stub"])
    lm = CappedLM("openrouter/cheap", max_subcalls=1, max_tokens=10)
    lm("a")
    lm.reset_subcalls()
    assert lm("b") == ["stub"]  # budget reset — no raise


async def test_capped_lm_async_path_also_charges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def stub(self: object, *a: object, **k: object) -> list[str]:
        return ["stub"]

    monkeypatch.setattr(dspy.LM, "acall", stub)
    lm = CappedLM("openrouter/cheap", max_subcalls=1, max_tokens=10)
    assert await lm.acall("a") == ["stub"]
    with pytest.raises(SubcallBudgetExceeded):
        await lm.acall("b")
