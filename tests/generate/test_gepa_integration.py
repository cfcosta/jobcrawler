"""Hermetic end-to-end GEPA integration (§4.11) — the contract unit tests can't reach.

`test_optimize.py` unit-tests our *decision logic* and GEPA *construction* in isolation
(injected factories, faked `detailed_results`). This module instead drives the **real**
gepa 0.0.27 engine through our production `gepa_compile`, proving the wiring holds when
the actual optimizer runs:

  1. `gepa_compile` returns a usable program (our 5-arg metric is accepted).
  2. The reflection LM is invoked.
  3. **Our metric's feedback string reaches the reflection LM's final, instruction-
     writing turn** — the entire premise of the Fix-2 feedback enrichment. GEPA shows
     the reflection LM only `(input, output, feedback)`; the numeric score is invisible,
     so feedback text is the *whole* optimization signal. If our metric returned `""`
     for sub-maximal candidates (the old bug), this turn would carry no signal.
  4. `track_stats=True` exposes `detailed_results.val_subscores` — the per-instance
     vectors the `_pareto_health_note` diagnostic reads.
  5. Both health diagnostics (`_pareto_health_note`, `_budget_health_note`) run
     against the **real** `DspyGEPAResult` without error — the unit tests exercise
     their logic over hand-built fakes; this proves the fakes match reality (dict-
     valued, possibly-sparse `val_subscores`; a real `candidates` list).

Hermetic by construction: `DummyLM` drives the task predictor and a `CapturingLM`
subclass drives the reflection LM, so there is no network, no API key, and no cost.
We do NOT assert that GEPA *improves* the program — DummyLM can't, and the task LM is
fixed, so no candidate can beat baseline by design. That's intentional: this guards the
LOOP and the FEEDBACK CHANNEL, not optimization quality (which needs a live LM).
"""

from __future__ import annotations

import logging
from typing import Any, cast

import dspy
import pytest
from dspy.utils import DummyLM

from bizup_autocrawler.generate.optimize import (
    _budget_health_note,
    _pareto_health_note,
    gepa_compile,
)

# A unique sentinel so we can prove *our* feedback — not GEPA's boilerplate — arrived.
FEEDBACK_MARK = "ITEMS_AXIS_LOW_FIND_A_FEED"


class _QA(dspy.Signature):
    """Answer the question."""

    question = dspy.InputField()
    answer = dspy.OutputField()


class _CapturingLM(DummyLM):
    """A DummyLM that records the FINAL turn of every prompt it completes. GEPA's
    native instruction proposer hands the reflection LM one instruction-writing turn
    embedding the current instruction and each example's (input, output, feedback);
    capturing that turn proves our feedback reached the LM that rewrites the prompt,
    without depending on `BaseLM.history`'s internal shape."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        super().__init__(answers)
        self.final_turns: list[str] = []

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        turns = messages or ([{"content": prompt}] if prompt is not None else [])
        self.final_turns.append(str(turns[-1].get("content", "")) if turns else "")
        return super().forward(prompt=prompt, messages=messages, **kwargs)


def _metric(
    gold: object,
    pred: object,
    trace: object = None,
    pred_name: str | None = None,
    pred_trace: object = None,
) -> dspy.Prediction:
    """A 5-arg GEPA metric mirroring `scoring.make_metric`: a sub-maximal candidate
    carries a non-empty feedback string (here, our sentinel) — never `""`/`None`."""
    got = str(getattr(pred, "answer", "")).strip().lower()
    if got == "blue":
        return dspy.Prediction(score=1.0, feedback="")
    return dspy.Prediction(
        score=0.2, feedback=f"{FEEDBACK_MARK}: answer blue, not '{got}'"
    )


@pytest.fixture
def _quiet_gepa() -> Any:
    """Silence GEPA's per-iteration INFO logging so a failure's captured output stays
    readable (the compile runs dozens of iterations). Restored after the test."""
    logger = logging.getLogger("dspy")
    prior = logger.level
    logger.setLevel(logging.ERROR)
    yield
    logger.setLevel(prior)


@pytest.mark.usefixtures("_quiet_gepa")
def test_gepa_compile_drives_real_engine_and_feedback_reaches_reflection() -> None:
    # Task LM: always answers "red" (wrong) → every candidate is sub-maximal, so the
    # metric always emits the sentinel and GEPA always tries to reflect.
    task_lm = DummyLM({"question": {"answer": "red"}})
    # Reflection LM: deliberately unmatchable dict key — we only need it invoked and
    # handed our feedback, not to produce a usable proposal (GEPA keeps the baseline).
    reflection_lm = _CapturingLM({"__never_matches__": {"improved_instruction": "x"}})

    student = dspy.Predict(_QA)
    trainset = [
        dspy.Example(question=f"q{i}: color of the sky?", answer="blue").with_inputs(
            "question"
        )
        for i in range(3)
    ]
    valset = [  # >= 2 so gepa_compile keeps the real Pareto selection strategy
        dspy.Example(question=f"v{i}: color of the sky?", answer="blue").with_inputs(
            "question"
        )
        for i in range(2)
    ]

    with dspy.context(lm=task_lm):
        optimized = gepa_compile(
            student,
            trainset=trainset,
            valset=valset,
            reflection_lm=cast(dspy.LM, reflection_lm),  # DummyLM is-a LM at runtime
            metric=_metric,
        )

    # 1. compile produced a usable program.
    assert isinstance(optimized, dspy.Module)

    # 2. the reflection LM was actually invoked.
    assert len(reflection_lm.final_turns) >= 1

    # 3. THE contract: our feedback reached the reflection LM's instruction-writing turn.
    turns_with_feedback = sum(
        FEEDBACK_MARK in turn for turn in reflection_lm.final_turns
    )
    assert turns_with_feedback >= 1, (
        "metric feedback never reached the reflection LM's final turn — the GEPA "
        "feedback channel is broken (Fix 2 regression)"
    )

    # 4. track_stats exposed the per-instance val subscores the health check reads.
    detailed = getattr(optimized, "detailed_results", None)
    assert getattr(detailed, "val_subscores", None) is not None

    # 5. Both diagnostics run against the REAL result without raising, and agree with
    #    the actual candidate count — proving the unit-test fakes match reality. The
    #    DummyLM reflection can't propose, so GEPA keeps baseline and the run is
    #    starved by construction; we derive the count rather than hard-coding it so the
    #    test is robust to GEPA's internal candidate bookkeeping.
    n_candidates = len(getattr(detailed, "candidates", []))
    budget_note = _budget_health_note(optimized)
    if n_candidates < 3:
        assert budget_note is not None
        assert f"explored only {n_candidates}" in budget_note
    else:
        assert budget_note is None
    # _pareto_health_note must not raise on the real (sparse, dict-valued) subscores;
    # it returns a note only if the valset never discriminated the candidates.
    pareto_note = _pareto_health_note(optimized)
    assert pareto_note is None or "discriminate" in pareto_note
