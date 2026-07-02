"""Tests for CandidateResult (§5) — the scoring-core output.

A frozen record produced by running one candidate crawler via
validate_crawler; every §4.11 adapter, the feedback transform, and §8
admission read these exact fields.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobcrawler.generate.scoring import CandidateResult, ExceptionInfo


def test_minimal_result() -> None:
    r = CandidateResult(item_count=3, pages_fetched=1)
    assert r.item_count == 3
    assert r.pages_fetched == 1
    assert r.samples == []
    assert r.schema_errors == []
    assert r.exception is None


def test_carries_samples_errors_and_exception() -> None:
    exc = ExceptionInfo(type="ValueError", message="bad", traceback_tail="…line 12")
    r = CandidateResult(
        item_count=0,
        pages_fetched=2,
        samples=[
            {
                "title": "T",
                "company": "C",
                "location": "L",
                "description": "D",
                "url": "u",
            }
        ],
        schema_errors=["missing description"],
        exception=exc,
    )
    assert r.samples[0]["title"] == "T"
    assert r.schema_errors == ["missing description"]
    assert r.exception is not None
    assert r.exception.type == "ValueError"


def test_is_frozen() -> None:
    r = CandidateResult(item_count=1, pages_fetched=1)
    with pytest.raises(ValidationError):
        r.item_count = 5  # type: ignore[misc]


@pytest.mark.parametrize("field", ["item_count", "pages_fetched"])
def test_counts_must_be_nonnegative(field: str) -> None:
    kwargs = {"item_count": 1, "pages_fetched": 1, field: -1}
    with pytest.raises(ValidationError):
        CandidateResult.model_validate(kwargs)
