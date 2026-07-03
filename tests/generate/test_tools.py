"""Tests for the generator host tools (§4.2)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import cast

import httpx

from jobcrawler.generate.scoring import CandidateResult, ExceptionInfo
from jobcrawler.generate.tools import (
    ats_api_candidates,
    corpus_host_evidence,
    find_feeds_in,
    make_host_tools,
    observed_hosts,
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


def test_host_validate_tool_clips_description_previews_with_a_marker() -> None:
    # The tool clips sample descriptions to previews for the RLM bridge budget
    # (§4.3) and MARKS the cut so the model cannot mistake a preview for a short —
    # or cross-sample identical — extraction. Presentation only: the §6 gate and
    # §4.11 axes grade the sandbox's full text, which stays unclipped upstream.
    def fake_validate(source: str, **_kw: object) -> CandidateResult:
        return CandidateResult(
            item_count=2,
            samples=[
                {
                    "url": "https://example.com/a",
                    "title": "Backend Engineer",
                    "description": "a" * 1000,
                },
                {
                    "url": "https://example.com/b",
                    "title": "Frontend Engineer",
                    "description": "b" * 200,
                },
            ],
            schema_errors=[],
            pages_fetched=1,
        )

    ctx = _ctx(lambda r: httpx.Response(404), host="example.com")
    _f, _fd, _sm, validate = make_host_tools(
        ctx, {"example.com"}, validate_fn=fake_validate
    )
    out = cast(dict, validate("SITE='x'\ndef crawl(ctx):\n    return []\n"))
    long_desc, short_desc = (
        cast(str, s["description"]) for s in cast("list[dict]", out["samples"])
    )
    assert long_desc.startswith("a" * 600)
    assert "+400 extracted chars are graded in full" in long_desc
    assert short_desc == "b" * 200  # under the preview head: untouched


def test_validate_tool_runs_under_the_admission_shaped_allowlist() -> None:
    # With registry/evidence split out, the tool validates each candidate under
    # the allowlist the COMMITTED crawler will get — registry + that candidate's
    # evidence-backed EXTRA_HOSTS — not the wider exploration envelope. An
    # undeclared cross-host fetch must fail here, in write → validate → fix,
    # rather than first at gate 1(b) after the fan-out picked the candidate.
    seen: dict[str, set[str]] = {}

    def fake_validate(
        source: str, *, allowed_hosts: object, **_kw: object
    ) -> CandidateResult:
        seen["hosts"] = set(cast("set[str]", allowed_hosts))
        return CandidateResult(
            item_count=0, samples=[], schema_errors=[], pages_fetched=0
        )

    ctx = _ctx(lambda r: httpx.Response(404), host="example.com")
    exploration = {"example.com", "api.example.net", "cdn.example.net"}
    _f, _fd, _sm, validate = make_host_tools(
        ctx,
        exploration,
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net", "cdn.example.net"],
        validate_fn=fake_validate,
    )
    validate(
        'SITE = "https://example.com"\n'
        'EXTRA_HOSTS = ["api.example.net"]\n'
        "def crawl(ctx):\n    yield 1\n"
    )
    # cdn.example.net is evidenced but undeclared: absent, exactly like admission.
    assert seen["hosts"] == {"example.com", "api.example.net"}


# -- §8 observed-host evidence --------------------------------------------------


def test_observed_hosts_extracts_literal_hosts_and_excludes_the_base() -> None:
    texts = [
        '<img src="https://cdn.assets.net/x.png"> https://jobs.example.com/a1',
        "plain text naming https://Data.Example.ORG/x too",
    ]
    hosts = observed_hosts(texts, "https://jobs.example.com/")
    assert "cdn.assets.net" in hosts
    assert "data.example.org" in hosts  # lowercased
    assert "jobs.example.com" not in hosts  # the base is already the allowlist


def test_observed_hosts_excludes_base_subdomains() -> None:
    hosts = observed_hosts(["https://api.example.com/x"], "https://example.com/")
    assert "api.example.com" not in hosts


def test_observed_hosts_never_contains_kernel_denied_names() -> None:
    # A served page naming an internal target never turns it into evidence —
    # the fixed SSRF kernel filters the channel itself (§8).
    texts = [
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://10.0.0.8/admin",
    ]
    hosts = observed_hosts(texts, "https://example.com/")
    assert "metadata.google.internal" not in hosts
    assert "10.0.0.8" not in hosts


def test_observed_hosts_infers_the_getro_api_host_from_cdn_assets() -> None:
    # jobs.madrona.com reality: the served HTML names cdn.getro.com dozens of
    # times and api.getro.com never (the client builds the API URL in JS). The
    # platform map bridges that gap, and platform-implied hosts rank first so a
    # bounded probe never trims them.
    texts = ['<img src="https://cdn.getro.com/logo.png">']
    hosts = observed_hosts(texts, "https://jobs.madrona.com/")
    assert "api.getro.com" in hosts
    assert hosts.index("api.getro.com") < hosts.index("cdn.getro.com")


def test_observed_hosts_includes_the_ats_api_candidates() -> None:
    hosts = observed_hosts([], "https://x.com/")
    assert "boards-api.greenhouse.io" in hosts
    assert "api.lever.co" in hosts


def test_corpus_host_evidence_walks_values_and_keys() -> None:
    # The apis map is keyed by URL — keys are evidence too.
    corpus = {
        "home": '<a href="https://cdn.getro.com/x">',
        "apis": {"https://api.other.net/v1/jobs": {"status": 200}},
    }
    hosts = corpus_host_evidence(corpus, "https://jobs.madrona.com/")
    assert "api.getro.com" in hosts
    assert "api.other.net" in hosts
