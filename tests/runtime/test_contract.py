"""Tests for the crawler contract — the normalized :class:`Job` (§4.6).

A Job is what a generated crawler yields and the runner validates before dedup/
upsert into the ``positions`` store. The five content strings plus ``source`` are
required and non-empty; everything else is extract-when-present (``None`` default).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jobcrawler.runtime.contract import Job

_REQUIRED = ["url", "title", "company", "location", "description", "source"]


def _fields() -> dict[str, object]:
    return {
        "url": "https://x.com/jobs/1",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Build APIs.",
        "source": "acme",
    }


def test_job_requires_only_core_fields() -> None:
    job = Job.model_validate(_fields())
    assert job.url == "https://x.com/jobs/1"
    assert job.company == "Acme"


def test_optional_fields_default_to_none() -> None:
    job = Job.model_validate(_fields())
    for field in (
        "salary",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "employment_type",
        "workplace_type",
        "department",
        "posted_at",
        "expires_at",
        "external_id",
        "apply_url",
    ):
        assert getattr(job, field) is None


def test_job_accepts_all_optional_fields() -> None:
    posted = datetime(2026, 6, 21, tzinfo=timezone.utc)
    expires = datetime(2026, 7, 21, tzinfo=timezone.utc)
    job = Job.model_validate(
        {
            **_fields(),
            "salary": "$150K – $200K",
            "salary_min": 150000.0,
            "salary_max": 200000.0,
            "salary_currency": "USD",
            "salary_period": "year",
            "employment_type": "Full-time",
            "workplace_type": "Remote",
            "department": "Platform",
            "posted_at": posted,
            "expires_at": expires,
            "external_id": "ats-42",
            "apply_url": "https://x.com/apply/1",
        }
    )
    assert job.salary_min == 150000.0
    assert job.posted_at == posted
    assert job.expires_at == expires
    assert job.external_id == "ats-42"
    assert job.apply_url == "https://x.com/apply/1"


@pytest.mark.parametrize("missing", _REQUIRED)
def test_job_rejects_missing_required(missing: str) -> None:
    fields = _fields()
    del fields[missing]
    with pytest.raises(ValidationError):
        Job.model_validate(fields)


@pytest.mark.parametrize("blank", _REQUIRED)
def test_job_rejects_blank_required(blank: str) -> None:
    fields = _fields()
    fields[blank] = ""
    with pytest.raises(ValidationError):
        Job.model_validate(fields)
