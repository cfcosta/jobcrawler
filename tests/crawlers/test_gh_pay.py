"""Greenhouse structured-pay capture (pay_transparency=true).

The boards API serves per-job ``pay_input_ranges`` — {min_cents, max_cents,
currency_type, title} — only when the list request carries
``pay_transparency=true``. A 2026-07-09 fleet probe found 21 committed boards
populated, so the fetch line must request the param and the per-job mapping
must collapse the bands into salary_min/salary_max/salary_currency
(single-currency boards only; salary_period stays unset so the host defaults
by currency).
"""

from __future__ import annotations

import json
from pathlib import Path

from jobcrawler.crawlers import anthropic, reddit

CRAWLERS = Path(__file__).resolve().parents[2] / "src" / "jobcrawler" / "crawlers"

# Boards the 2026-07-09 live probe found serving populated pay_input_ranges.
PROBED_TOKENS = (
    "anthropic",
    "remotecom",
    "toast",
    "okta",
    "mongodb",
    "elastic",
    "reddit",
    "scaleai",
    "figma",
    "robinhood",
    "fivetran",
    "clickhouse",
    "gitlab",
    "duolingo",
    "algolia",
    "tailscale",
    "fireworksai",
    "chainguard",
    "cockroachlabs",
    "honeycomb",
    "circleci",
)


class _FakeCtx:
    """Feeds one canned Greenhouse boards-API payload; records fetched URLs."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str, **_: object) -> str:
        self.urls.append(url)
        return json.dumps(self._payload)

    def render(self, url: str) -> str:  # pragma: no cover - unused
        return ""

    def log(self, *a: object, **k: object) -> None:  # pragma: no cover - unused
        pass


def _payload(job: dict[str, object]) -> dict[str, object]:
    return {"jobs": [job]}


def _job(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "id": 7001,
        "title": "Senior Backend Engineer",
        "absolute_url": "https://job-boards.greenhouse.io/board/jobs/7001",
        "location": {"name": "Remote - United States"},
        "content": "&lt;p&gt;Ship Python services at scale for a global community.&lt;/p&gt;",
    }
    job.update(overrides)
    return job


def test_reddit_maps_pay_input_ranges() -> None:
    # Two USD bands (one per posting region) collapse to min-of-mins /
    # max-of-maxes in whole currency units, cents dropped.
    ctx = _FakeCtx(
        _payload(
            _job(
                pay_input_ranges=[
                    {
                        "min_cents": 23000000,
                        "max_cents": 32200000,
                        "currency_type": "USD",
                        "title": "US Pay Range",
                    },
                    {
                        "min_cents": 25000000,
                        "max_cents": 30000000,
                        "currency_type": "USD",
                        "title": "NYC Pay Range",
                    },
                ]
            )
        )
    )
    jobs = list(reddit.crawl(ctx))
    assert len(jobs) == 1
    out = jobs[0]
    assert out["salary_min"] == 230000.0
    assert out["salary_max"] == 322000.0
    assert out["salary_currency"] == "USD"
    # The host defaults period by currency; the crawler must not guess one.
    assert "salary_period" not in out


def test_anthropic_multi_currency_ranges_stay_unstated() -> None:
    # Mixed-currency bands (one per region) cannot collapse into one honest
    # band, so the job carries no salary keys at all.
    ctx = _FakeCtx(
        _payload(
            _job(
                pay_input_ranges=[
                    {
                        "min_cents": 30000000,
                        "max_cents": 40000000,
                        "currency_type": "USD",
                        "title": "US Pay Range",
                    },
                    {
                        "min_cents": 25000000,
                        "max_cents": 33000000,
                        "currency_type": "GBP",
                        "title": "UK Pay Range",
                    },
                ]
            )
        )
    )
    jobs = list(anthropic.crawl(ctx))
    assert len(jobs) == 1
    out = jobs[0]
    assert "salary_min" not in out
    assert "salary_max" not in out
    assert "salary_currency" not in out
    assert "salary_period" not in out


def test_fetch_requests_pay_transparency() -> None:
    ctx = _FakeCtx(_payload(_job()))
    list(reddit.crawl(ctx))
    assert ctx.urls, "crawl() never fetched"
    assert all("pay_transparency=true" in url for url in ctx.urls)


def test_every_probed_board_requests_pay_transparency() -> None:
    # Repo-level pin: every probed board with a committed crawler keeps
    # requesting the param — dropping it silently loses structured pay.
    sources = {
        path: path.read_text(encoding="utf-8") for path in sorted(CRAWLERS.glob("*.py"))
    }
    missing_crawler: list[str] = []
    missing_param: list[str] = []
    for token in PROBED_TOKENS:
        needle = f"boards-api.greenhouse.io/v1/boards/{token}/"
        files = [path for path, text in sources.items() if needle in text]
        if not files:
            # No committed crawler for this board — nothing to pin yet.
            missing_crawler.append(token)
            continue
        missing_param.extend(
            f"{token}: {path.name}"
            for path in files
            if "pay_transparency=true" not in sources[path]
        )
    assert missing_param == []
    # All 21 probed boards had committed crawlers when this pin landed; a
    # token showing up here means the board's crawler was removed or renamed.
    assert missing_crawler == []
