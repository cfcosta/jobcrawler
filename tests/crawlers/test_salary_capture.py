"""Fleet-level pins for crawler salary capture (§4.6).

The 13-crawler ``salary_interval`` incident: generated Ashby crawlers invented a
key the Job contract doesn't have, pydantic silently dropped it, and 433 live
rows stored period='year' regardless of what the board said. The key is renamed
fleet-wide, the §6 gate now rejects the token at admission, and the host maps
the raw Ashby interval vocabulary — so these greps are the regression fence.
"""

from __future__ import annotations

from pathlib import Path

CRAWLERS = Path(__file__).resolve().parents[2] / "src" / "jobcrawler" / "crawlers"


def test_no_crawler_emits_the_invented_salary_interval_key() -> None:
    offenders = [
        path.name
        for path in sorted(CRAWLERS.glob("*.py"))
        if "salary_interval" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_generator_teaching_names_the_salary_contract_keys() -> None:
    # New boards emit salary in unpredictable shapes unless the prompt names the
    # exact contract keys — 'salary' alone is how salary_interval got invented.
    source = (
        CRAWLERS.parents[1] / "jobcrawler" / "generate" / "signatures.py"
    ).read_text(encoding="utf-8")
    for literal in (
        "salary_min/salary_max/salary_currency/salary_period",
        "never `salary_interval`",
        "pay_transparency=true",
    ):
        assert literal in source, literal
