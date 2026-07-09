"""Wave-3 salary capture: verbatim text + structured quartet (§4.6 dual pattern).

Crawlers whose sources serve structured compensation must yield BOTH the printed
salary text and the salary_min/salary_max/salary_currency/salary_period quartet,
omitting zero/absent bounds at the source (a zero bound means "unstated"). The
worst regression is cloudwalk: Lever serves salaryRange as an object, and a dict
assigned into the str-typed salary field fails contract validation at the runner,
dropping the whole posting.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import (
    a16z_portfolio_jobs,
    cloudwalk,
    devjobsscanner_rust,
    jobicy_developer,
    openai,
    paradigm_portfolio_jobs,
    quickwit,
    remote3,
    valor_capital,
)


class _FakeCtx:
    """Serves canned fetch payloads in order; the last one repeats."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def fetch(self, url: str, **_: object) -> str:
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    def render(self, url: str) -> str:  # pragma: no cover - unused
        return ""

    def log(self, *a: object, **k: object) -> None:  # pragma: no cover - unused
        pass


# --- cloudwalk (Lever): dict salaryRange must map, not kill the posting ---


def _cloudwalk_posting(salary_range: object) -> dict[str, object]:
    return {
        "id": "cw-1",
        "text": "Software Engineer",
        "hostedUrl": "https://jobs.lever.co/cloudwalk/cw-1",
        "applyUrl": "https://jobs.lever.co/cloudwalk/cw-1/apply",
        "categories": {
            "location": "Remote, Brazil",
            "team": "Engineering",
            "commitment": "Full-time",
        },
        "description": "<p>Build payment rails.</p>",
        "createdAt": 1751328000000,
        "salaryRange": salary_range,
    }


def test_cloudwalk_maps_a_dict_salary_range_instead_of_dropping_the_posting() -> None:
    postings = [
        _cloudwalk_posting(
            {"min": 100000, "max": 150000, "currency": "USD", "interval": "per-year-salary"}
        )
    ]
    jobs = list(cloudwalk.crawl(_FakeCtx([json.dumps(postings)])))
    assert len(jobs) == 1  # the posting survives
    job = jobs[0]
    assert isinstance(job["salary"], str)  # never a dict into the str-typed field
    assert job["salary"] == "USD 100000 - 150000 / per-year-salary"
    assert job["salary_min"] == 100000.0
    assert job["salary_max"] == 150000.0
    assert job["salary_currency"] == "USD"
    assert job["salary_period"] == "per-year-salary"  # as stated; the host maps it


def test_cloudwalk_keeps_a_string_salary_range_verbatim() -> None:
    postings = [_cloudwalk_posting("R$ 8.000 - R$ 12.000 / mes")]
    (job,) = cloudwalk.crawl(_FakeCtx([json.dumps(postings)]))
    assert job["salary"] == "R$ 8.000 - R$ 12.000 / mes"
    assert "salary_min" not in job


def test_cloudwalk_omits_zero_bounds_from_the_quartet() -> None:
    postings = [
        _cloudwalk_posting(
            {"min": 0, "max": 150000, "currency": "USD", "interval": "per-year-salary"}
        )
    ]
    (job,) = cloudwalk.crawl(_FakeCtx([json.dumps(postings)]))
    assert "salary_min" not in job  # zero means "unstated" — omit at the source
    assert job["salary_max"] == 150000.0
    assert job["salary"] == "USD 150000 / per-year-salary"


# --- openai (Ashby flattener): quartet from summaryComponents, text preserved ---


def _openai_job(components: list[dict[str, object]]) -> dict[str, object]:
    return {
        "title": "Research Engineer",
        "jobUrl": "https://jobs.ashbyhq.com/openai/re-1",
        "location": "San Francisco",
        "descriptionPlain": "Do research engineering.",
        "isListed": True,
        "compensation": {"summaryComponents": components},
    }


def test_openai_sets_the_quartet_with_the_interval_as_stated() -> None:
    components: list[dict[str, object]] = [
        {
            "compensationType": "Salary",
            "minValue": 150000,
            "maxValue": 200000,
            "currencyCode": "USD",
            "interval": "1 YEAR",
        }
    ]
    payload = {"jobs": [_openai_job(components)]}
    (job,) = openai.crawl(_FakeCtx([json.dumps(payload)]))
    assert job["salary"] == "USD 150000 - 200000 / 1 YEAR"  # verbatim text preserved
    assert job["salary_min"] == 150000.0
    assert job["salary_max"] == 200000.0
    assert job["salary_currency"] == "USD"
    assert job["salary_period"] == "1 YEAR"  # as stated; the host maps '1 YEAR'


def test_openai_skips_the_quartet_when_components_disagree_on_currency() -> None:
    components: list[dict[str, object]] = [
        {
            "compensationType": "Salary",
            "minValue": 150000,
            "maxValue": 200000,
            "currencyCode": "USD",
            "interval": "1 YEAR",
        },
        {
            "compensationType": "Salary",
            "minValue": 140000,
            "maxValue": 190000,
            "currencyCode": "EUR",
            "interval": "1 YEAR",
        },
    ]
    payload = {"jobs": [_openai_job(components)]}
    (job,) = openai.crawl(_FakeCtx([json.dumps(payload)]))
    assert "salary_min" not in job
    assert "salary_max" not in job
    assert "salary_currency" not in job
    assert "salary_period" not in job
    assert job["salary"]  # the flattened text still flows to the host parser


# --- devjobsscanner: the API sends salaryMin=0 for unstated ---


def test_devjobsscanner_omits_the_zero_min_bound() -> None:
    payload = {
        "jobs": [
            {
                "id": "djs-1",
                "title": "Rust Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://boards.example/rust-1",
                "salaryCurrency": "USD",
                "salaryMin": 0,
                "salaryMax": 120000,
            }
        ]
    }
    ctx = _FakeCtx([json.dumps(payload), json.dumps({"jobs": []})])
    (job,) = devjobsscanner_rust.crawl(ctx)
    assert "salary_min" not in job  # 0 would otherwise mint 0-X rows
    assert job["salary_max"] == 120000.0
    assert job["salary_currency"] == "USD"
    assert job["salary"] == "USD 0-120000"  # verbatim text unchanged


# --- jobicy: full structured quartet flattened into text today ---


def test_jobicy_maps_the_full_quartet() -> None:
    payload = {
        "jobs": [
            {
                "id": 4242,
                "jobTitle": "Backend Developer",
                "url": "https://jobicy.com/jobs/backend-4242",
                "companyName": "Acme",
                "jobGeo": "Remote, Worldwide",
                "jobDescription": "<p>" + "Build APIs. " * 30 + "</p>",
                "salaryMin": 60000,
                "salaryMax": 90000,
                "salaryCurrency": "USD",
                "salaryPeriod": "yearly",
            }
        ]
    }
    (job,) = jobicy_developer.crawl(_FakeCtx([json.dumps(payload)]))
    assert job["salary"] == "USD 60000-90000 yearly"
    assert job["salary_min"] == 60000.0
    assert job["salary_max"] == 90000.0
    assert job["salary_currency"] == "USD"
    assert job["salary_period"] == "yearly"  # as stated; the host maps wording


# --- Getro flatteners: quartet added next to the joined text ---


def test_a16z_salary_fields_add_the_quartet_to_the_flattened_text() -> None:
    fields = a16z_portfolio_jobs._salary_fields(
        {
            "minValue": 120000,
            "maxValue": 160000,
            "currency": {"value": "USD"},
            "period": {"value": "year"},
        }
    )
    assert fields["salary"] == "120000-160000 USD per year"
    assert fields["salary_min"] == 120000.0
    assert fields["salary_max"] == 160000.0
    assert fields["salary_currency"] == "USD"
    assert fields["salary_period"] == "year"


def test_paradigm_salary_fields_add_the_quartet_to_the_flattened_text() -> None:
    fields = paradigm_portfolio_jobs._salary_fields(
        {"minValue": 150000, "maxValue": 200000, "currency": "USD", "period": "year"}
    )
    assert fields["salary"] == "150000 - 200000 USD per year"
    assert fields["salary_min"] == 150000.0
    assert fields["salary_max"] == 200000.0
    assert fields["salary_currency"] == "USD"
    assert fields["salary_period"] == "year"


# --- remote3 / valor: structured source fields flattened to text ---


def test_remote3_omits_zero_bounds_and_keeps_usd_year() -> None:
    fields = remote3._salary_fields({"salary_min": 0, "salary_max": 80000})
    assert "salary_min" not in fields
    assert fields["salary_max"] == 80000.0
    assert fields["salary_currency"] == "USD"  # per the current text builder
    assert fields["salary_period"] == "year"
    assert fields["salary"] == "USD 0 - 80000 per year"  # text stays verbatim


def test_valor_capital_maps_jsonld_base_salary() -> None:
    data: dict[str, object] = {
        "baseSalary": {
            "currency": "BRL",
            "value": {"minValue": 8000, "maxValue": 12000, "unitText": "MONTH"},
        }
    }
    fields = valor_capital.salary_fields_from_jsonld(data)
    assert fields["salary"] == "8000-12000 BRL MONTH"
    assert fields["salary_min"] == 8000.0
    assert fields["salary_max"] == 12000.0
    assert fields["salary_currency"] == "BRL"
    assert fields["salary_period"] == "MONTH"  # unitText passes as stated


# --- quickwit: description miner needs compensation context ---


def test_quickwit_captures_salary_with_compensation_context() -> None:
    assert (
        quickwit._salary("Compensation: €80k-110k depending on experience")
        == "€80k-110k"
    )


def test_quickwit_ignores_bare_funding_figures() -> None:
    assert quickwit._salary("We just raised $12.5M to build the future of search.") is None
