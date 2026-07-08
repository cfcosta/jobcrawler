"""Tests for the §4.2 generation LLM-log capture (`jobcrawler.generate.lm_capture`).

The callback records each generation LM call into a thread-local buffer scoped by
begin/end_capture. These drive the callback directly (dspy fires it around every LM
``__call__``/``acall``, confirmed against the dspy API) to prove: start/end pairing,
error capture, per-field truncation, and that it is inert outside an active capture.
"""

from __future__ import annotations

from typing import Any

from jobcrawler.generate import lm_capture as cap


class _FakeLM:
    model = "openrouter/test/model"


def _fire(cb: Any, call_id: str, prompt_msg: str, outputs: object, exc: object = None) -> None:
    cb.on_lm_start(call_id, _FakeLM(), {"messages": [{"role": "user", "content": prompt_msg}]})
    cb.on_lm_end(call_id, outputs, exc)


def test_capture_pairs_prompt_and_completion() -> None:
    cap.begin_capture()
    cb = cap.capture_callback()
    _fire(cb, "c1", "generate a crawler", ["def crawl(ctx): ..."])
    log = cap.end_capture()
    assert len(log) == 1
    (call,) = log
    assert call["model"] == "openrouter/test/model"
    assert "generate a crawler" in call["prompt"]
    assert call["completion"] == "def crawl(ctx): ..."
    assert call["error"] is None


def test_capture_records_a_failed_call_as_error() -> None:
    cap.begin_capture()
    cb = cap.capture_callback()
    _fire(cb, "c1", "retry", None, exc=OSError("[Errno 24] Too many open files"))
    (call,) = cap.end_capture()
    assert call["completion"] is None
    assert call["error"] == "OSError: [Errno 24] Too many open files"


def test_capture_is_a_noop_outside_an_active_capture() -> None:
    # No begin_capture(): the callback must not record (it rides on every LM, incl. the matcher).
    cb = cap.capture_callback()
    _fire(cb, "c1", "hi", ["out"])
    assert cap.end_capture() == []


def test_capture_truncates_oversized_fields() -> None:
    cap.begin_capture()
    cb = cap.capture_callback()
    huge = "x" * (cap._MAX_FIELD + 5000)
    _fire(cb, "c1", huge, [huge])
    (call,) = cap.end_capture()
    assert len(call["prompt"]) < len(huge) and "truncated" in call["prompt"]
    assert len(call["completion"]) < len(huge) and "truncated" in call["completion"]


def test_end_capture_resets_so_the_next_attempt_starts_clean() -> None:
    cap.begin_capture()
    _fire(cap.capture_callback(), "c1", "a", ["b"])
    assert len(cap.end_capture()) == 1
    # a fresh attempt on the same thread sees none of the prior attempt's calls
    cap.begin_capture()
    assert cap.end_capture() == []
