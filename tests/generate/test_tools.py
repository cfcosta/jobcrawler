"""Tests for the generator host tools (§4.2)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import cast

import httpx

from jobcrawler.generate.scoring import CandidateResult, ExceptionInfo
from jobcrawler.generate.tools import (
    ats_api_candidates,
    find_feeds_in,
    make_host_tools,
)
from jobcrawler.runtime.ctx import Ctx

_BASE = "https://example.com/careers"
Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(handler: Handler, host: str = "x.com") -> Ctx:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return Ctx({host}, client=client)


def test_finds_rss_and_atom_feeds_as_absolute_urls() -> None:
    html = """
    <head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="https://example.com/atom">
    </head>
    """
    assert find_feeds_in(html, _BASE) == [
        "https://example.com/feed.xml",
        "https://example.com/atom",
    ]


def test_finds_json_feed() -> None:
    html = '<link rel="alternate" type="application/feed+json" href="/feed.json">'
    assert find_feeds_in(html, _BASE) == ["https://example.com/feed.json"]


def test_ignores_non_feed_links() -> None:
    html = (
        '<link rel="stylesheet" href="/style.css">'
        '<link rel="icon" href="/favicon.ico">'
        '<link rel="canonical" href="/careers">'
    )
    assert find_feeds_in(html, _BASE) == []


def test_dedupes_repeated_feed_declarations() -> None:
    html = (
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    )
    assert find_feeds_in(html, _BASE) == ["https://example.com/feed.xml"]


def test_no_feeds_returns_empty() -> None:
    assert find_feeds_in("<html><body>no feeds here</body></html>", _BASE) == []


# -- known-ATS whole-board API discovery (§4.2) ------------------------------


def test_ats_candidate_for_greenhouse_board_host() -> None:
    # Already on the board: the platform is known, so a single exact endpoint.
    assert ats_api_candidates("https://boards.greenhouse.io/spotify") == [
        "https://boards-api.greenhouse.io/v1/boards/spotify/jobs?content=true"
    ]


def test_ats_candidate_for_lever_and_ashby_board_hosts() -> None:
    assert ats_api_candidates("https://jobs.lever.co/acme") == [
        "https://api.lever.co/v0/postings/acme?mode=json"
    ]
    assert ats_api_candidates("https://jobs.ashbyhq.com/acme") == [
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
    ]


def test_ats_candidate_for_gupy_vhost() -> None:
    # Gupy embeds the whole board in the page's __NEXT_DATA__, so the board page
    # itself is the enumerate-everything source.
    assert ats_api_candidates("https://acme.gupy.io/") == ["https://acme.gupy.io/"]


def test_ats_candidates_for_company_domain_offer_every_platform() -> None:
    # Platform unknown: guess the org slug and offer one candidate per platform.
    assert ats_api_candidates("https://acme.com/careers") == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true",
        "https://api.lever.co/v0/postings/acme?mode=json",
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true",
        "https://acme.gupy.io/",
    ]


def test_ats_candidates_strip_careers_subdomain_for_the_slug() -> None:
    got = ats_api_candidates("https://careers.acme.com/")
    assert got[0] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"


def test_ats_candidates_empty_when_no_org_slug() -> None:
    assert ats_api_candidates("https://localhost/") == []
    assert ats_api_candidates("not a url") == []


# -- host tools handed to dspy.RLM(tools=...) --------------------------------


def test_host_tools_fetch_and_find_feeds() -> None:
    pages = {
        "/": '<link rel="alternate" type="application/rss+xml" href="/feed">',
        "/feed": "<rss/>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    fetch, find_feeds, _get_sitemap, _validate = make_host_tools(
        _ctx(handler), {"x.com"}
    )
    assert cast(str, fetch("https://x.com/")).startswith("<link")
    feeds = cast("list[str]", find_feeds("https://x.com/"))
    # The linked RSS feed leads; the host's ATS API candidates are appended so the
    # generator can prefer an enumerate-the-board source over scraping (§4.2).
    assert feeds[0] == "https://x.com/feed"
    assert "https://boards-api.greenhouse.io/v1/boards/x/jobs?content=true" in feeds


def test_host_validate_tool_runs_the_sandbox_and_reshapes_the_result() -> None:
    # tools.validate_crawler's job is to run the §4.4 sandbox and reshape its
    # CandidateResult into the RLM-facing dict. Inject a fake validator so the
    # wiring is tested deterministically without spawning the subprocess (mirrors
    # scoring's injected core); the sandbox's own Job-counting has its own tests.
    calls: list[tuple[str, set[str], float]] = []

    def fake_validate(
        source: str, *, allowed_hosts: Collection[str], timeout: float
    ) -> CandidateResult:
        calls.append((source, set(allowed_hosts), timeout))
        return CandidateResult(
            item_count=1,
            samples=[
                {
                    "url": "https://example.com/a",
                    "title": "Backend Engineer",
                    "description": "x" * 500,
                }
            ],
            schema_errors=[],
            pages_fetched=1,
        )

    ctx = _ctx(lambda r: httpx.Response(404), host="example.com")
    _fetch, _find, _sitemap, validate = make_host_tools(
        ctx, {"example.com"}, validate_timeout=7.0, validate_fn=fake_validate
    )
    out = cast(dict, validate("SITE='x'\ndef crawl(ctx):\n    return []\n"))
    assert out["item_count"] == 1
    assert out["samples"][0]["title"] == "Backend Engineer"
    assert out["exception"] is None
    # The tool forwards the source + this site's hosts + configured timeout verbatim.
    assert calls == [
        ("SITE='x'\ndef crawl(ctx):\n    return []\n", {"example.com"}, 7.0)
    ]


def test_host_validate_tool_surfaces_missing_job_fields_and_exception() -> None:
    # A candidate that yields incomplete postings comes back with schema_errors
    # naming the missing Job fields (company/location), and a crash is reshaped to
    # {type, message} (the traceback tail is dropped) — the write→validate→fix signal.
    def fake_validate(source: str, **_kw: object) -> CandidateResult:
        return CandidateResult(
            item_count=0,
            samples=[],
            schema_errors=["company: Field required", "location: Field required"],
            pages_fetched=0,
            exception=ExceptionInfo(
                type="ValueError", message="bad board", traceback_tail="…frames…"
            ),
        )

    ctx = _ctx(lambda r: httpx.Response(404), host="example.com")
    _f, _fd, _sm, validate = make_host_tools(
        ctx, {"example.com"}, validate_fn=fake_validate
    )
    out = cast(dict, validate("SITE='x'\ndef crawl(ctx):\n    return []\n"))
    assert out["item_count"] == 0
    assert out["schema_errors"] == [
        "company: Field required",
        "location: Field required",
    ]
    assert out["exception"] == {"type": "ValueError", "message": "bad board"}
