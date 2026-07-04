"""Tests for snapshot/corpus shaping (§4.2, §9).

The generator reasons over a *prefetched* board snapshot injected into the RLM
REPL as a real variable. dspy serializes it as a Python literal and raises on any
non-JSON type, so the corpus must be an addressable dict of JSON primitives
(addressable parts beat one blob — fewer tokens). Pyodide lacks grep/sed, so we
ship plain-Python navigation helpers the prompt references.

The job-board specifics are ATS detection: a host/URL pattern that names a known
ATS (Greenhouse/Lever/Ashby/Gupy) becomes a candidate structured-API URL in the
corpus, and when one answers during prefetch we sample real postings and the
board's open-role count.
"""

from __future__ import annotations

import ast
import json
from typing import cast

import httpx
import pytest

from jobcrawler.generate.snapshot import (
    REPL_HELPERS,
    ats_api_candidates,
    build_corpus,
    count_sitemap_urls,
    ensure_json_primitive,
    on_domain_links,
    pick_jobs_sitemap,
    prefetch_corpus,
    sitemaps_from_robots,
)
from jobcrawler.runtime.ctx import Ctx


def test_corpus_has_addressable_shape() -> None:
    corpus = build_corpus(
        homepage="<html>home</html>",
        robots="User-agent: *",
        sitemap="<urlset/>",
        postings=[{"url": "https://x.com/a", "title": "A", "text": "body"}],
    )
    assert set(corpus) == {
        "homepage",
        "robots",
        "sitemap",
        "sitemaps",
        "expected_positions",
        "apis",
        "postings",
        "feeds",
    }
    postings = corpus["postings"]
    assert isinstance(postings, list)
    assert postings[0] == {"url": "https://x.com/a", "title": "A", "text": "body"}


def test_corpus_is_json_serializable() -> None:
    corpus = build_corpus(
        homepage="h", postings=[{"url": "u", "title": "t", "text": "x"}]
    )
    # dspy injects it as a literal; if json.dumps works, the literal step will too.
    assert json.loads(json.dumps(corpus)) == corpus


def test_postings_coerced_to_primitive_fields() -> None:
    corpus = build_corpus(homepage="h", postings=[{"url": 123, "title": None}])
    postings = corpus["postings"]
    assert isinstance(postings, list)
    assert postings[0] == {"url": "123", "title": "", "text": ""}


def test_defaults_are_empty_strings_and_list() -> None:
    corpus = build_corpus(homepage="h")
    assert corpus["robots"] == ""
    assert corpus["sitemap"] == ""
    assert corpus["postings"] == []
    assert corpus["sitemaps"] == []
    assert corpus["expected_positions"] == 0
    assert corpus["apis"] == []


# -- ATS detection -----------------------------------------------------------


def test_ats_api_candidates_greenhouse_board_url() -> None:
    assert ats_api_candidates("https://boards.greenhouse.io/acme") == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    ]


def test_ats_api_candidates_greenhouse_from_homepage_embed() -> None:
    # A company careers page that iframes its Greenhouse board — the token is read
    # from the embed's for= param, not the site host.
    home = (
        '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco">'
        "</iframe>"
    )
    assert ats_api_candidates("https://acme.com/careers", home) == [
        "https://boards-api.greenhouse.io/v1/boards/acmeco/jobs?content=true"
    ]


def test_ats_api_candidates_lever_board_url() -> None:
    assert ats_api_candidates("https://jobs.lever.co/acme") == [
        "https://api.lever.co/v0/postings/acme?mode=json"
    ]


def test_ats_api_candidates_ashby_board_url() -> None:
    assert ats_api_candidates("https://jobs.ashbyhq.com/acme") == [
        "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
    ]


def test_ats_api_candidates_gupy_board_url() -> None:
    # Gupy's companyId lives in the board __NEXT_DATA__; the portal endpoint is still
    # surfaced as the candidate the generator fills in.
    assert ats_api_candidates("https://acme.gupy.io/") == [
        "https://employability-portal.gupy.io/api/v1/jobs?limit=100"
    ]


def test_ats_api_candidates_empty_on_a_plain_careers_page() -> None:
    # A hand-rolled careers page names no ATS — the generator gets no false candidate.
    assert (
        ats_api_candidates("https://example.com/careers", "<html>Join our team</html>")
        == []
    )


# -- sitemaps / robots -------------------------------------------------------


def test_sitemaps_from_robots_parses_sitemap_lines() -> None:
    robots = (
        "User-agent: *\nDisallow: /x\n"
        "Sitemap: https://x.com/jobs.xml\n"
        "sitemap: https://x.com/sitemap.xml\n"  # case-insensitive key
    )
    assert sitemaps_from_robots(robots) == [
        "https://x.com/jobs.xml",
        "https://x.com/sitemap.xml",
    ]


def test_pick_jobs_sitemap_prefers_the_jobs_one() -> None:
    assert (
        pick_jobs_sitemap(["https://x.com/sitemap.xml", "https://x.com/jobs.xml"])
        == "https://x.com/jobs.xml"
    )
    assert (
        pick_jobs_sitemap(["https://x.com/sitemap.xml"]) == "https://x.com/sitemap.xml"
    )
    assert pick_jobs_sitemap([]) is None


def test_count_sitemap_urls_counts_locs_but_not_indexes() -> None:
    flat = "<urlset><url><loc>a</loc></url><url><loc>b</loc></url></urlset>"
    assert count_sitemap_urls(flat) == 2
    index = "<sitemapindex><sitemap><loc>child.xml</loc></sitemap></sitemapindex>"
    assert count_sitemap_urls(index) == 0  # an index counts no postings


def test_ensure_json_primitive_accepts_nested_primitives() -> None:
    ensure_json_primitive({"a": [1, 2.0, "x", True, None], "b": {"c": "d"}})  # no raise


def test_ensure_json_primitive_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        ensure_json_primitive({"bad": {1, 2, 3}})  # a set is not JSON-able


def test_repl_helpers_are_valid_python_defining_grep() -> None:
    tree = ast.parse(REPL_HELPERS)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "grep" in defined


def test_dspy_serializer_rejects_non_json_corpus() -> None:
    # §9/§12 substitute-for-smoke-test: dspy's corpus injection raises on a
    # non-JSON type — the behavior ensure_json_primitive mirrors, verified live.
    from dspy.primitives.python_interpreter import (
        CodeInterpreterError,
        PythonInterpreter,
    )

    class _Weird: ...

    with pytest.raises(CodeInterpreterError):
        PythonInterpreter()._to_json_compatible(_Weird())


def test_on_domain_links_filters_and_dedupes() -> None:
    html = (
        '<a href="/a1">A1</a> <a href="/a2">A2</a> <a href="/a1">dup</a>'
        '<a href="https://evil.com/x">off</a>'
    )
    assert on_domain_links(html, "https://x.com/") == [
        "https://x.com/a1",
        "https://x.com/a2",
    ]


# -- prefetch ----------------------------------------------------------------


def test_prefetch_corpus_assembles_from_ctx() -> None:
    pages = {
        "/": (
            '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
            '<a href="/a1">A1</a><a href="/a2">A2</a>'
        ),
        "/robots.txt": "User-agent: *",
        "/sitemap.xml": "<urlset/>",
        "/a1": "Body of A1",
        "/a2": "Body of A2",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://x.com/", ctx)

    assert corpus["robots"] == "User-agent: *"
    assert corpus["sitemap"] == "<urlset/>"
    assert corpus["apis"] == []  # not a known ATS -> scrape fallback
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert {p["url"] for p in postings} == {"https://x.com/a1", "https://x.com/a2"}
    assert {p["text"] for p in postings} == {"Body of A1", "Body of A2"}
    assert corpus["feeds"] == ["https://x.com/feed.xml"]  # feed discovery


def test_prefetch_survives_an_entrypoint_that_refuses_the_bare_get() -> None:
    # A registry URL can be an API index that 403s a naked GET (idealist's Algolia
    # root wants its search-key headers — only the generated crawler sends them, per
    # the operator hints). The refusal must land IN the corpus as a fact the model
    # reads, not crash the bootstrap before generation ever starts.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://x.com/", ctx)

    homepage = cast("str", corpus["homepage"])
    assert homepage.startswith("[unfetchable: GET https://x.com/")
    assert "403" in homepage
    assert corpus["postings"] == []  # nothing sampled — and nothing raised


def test_prefetch_clips_oversized_content_but_discovers_from_the_full_page() -> None:
    # Raw pages run to 100s of KB; the snapshot must bound them (§9) or a reasoning
    # LM burns its whole token budget. Link discovery still uses the full homepage,
    # so a clip that drops late links doesn't cost posting samples.
    homepage = '<a href="/a1">A1</a>' + "x" * 200_000  # link is early, body is huge
    pages = {
        "/": homepage,
        "/robots.txt": "",
        "/sitemap.xml": "",
        "/a1": "y" * 200_000,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus(
        "https://x.com/", ctx, homepage_chars=1000, posting_chars=500
    )

    assert len(str(corpus["homepage"])) == 1000  # clipped
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert postings  # still discovered the link from the full page
    assert all(len(p["text"]) <= 500 for p in postings)  # each sample clipped


def test_prefetch_discovers_jobs_sitemap_and_counts_expected_positions() -> None:
    # robots advertises a jobs sitemap; prefetch fetches it (not the generic
    # /sitemap.xml) and counts its <loc> entries as the enumeration target (§6).
    jobs_xml = (
        "<urlset>"
        + "".join(f"<url><loc>https://x.com/j/{i}.html</loc></url>" for i in range(40))
        + "</urlset>"
    )
    pages = {
        "/": "<a href='/a1'>A1</a>",
        "/robots.txt": (
            "User-agent: *\n"
            "Sitemap: https://x.com/jobs.xml\n"
            "Sitemap: https://x.com/sitemap.xml\n"
        ),
        "/jobs.xml": jobs_xml,
        "/a1": "Body",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://x.com/", ctx)

    assert corpus["expected_positions"] == 40  # the jobs sitemap's posting count
    assert corpus["sitemap"] == jobs_xml  # the jobs sitemap text, for the RLM to use
    sitemaps = cast("list[str]", corpus["sitemaps"])
    assert "https://x.com/jobs.xml" in sitemaps


def test_prefetch_samples_postings_from_the_ats_api() -> None:
    # A Greenhouse board: surface the structured API, take real posting samples from
    # it (full content), and read meta.total as the enumeration target (§4.2, §6).
    board = {
        "meta": {"total": 42},
        "jobs": [
            {
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "title": "Senior Backend Engineer",
                "content": "&lt;p&gt;Build the whole thing.&lt;/p&gt;",
                "location": {"name": "Remote"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=board)
        if request.url.path == "/":
            return httpx.Response(200, text="<html>Acme careers</html>")
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"boards.greenhouse.io", "boards-api.greenhouse.io"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://boards.greenhouse.io/acme", ctx)

    assert corpus["apis"] == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    ]
    assert corpus["expected_positions"] == 42  # meta.total, the enumeration target
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert postings[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert postings[0]["title"] == "Senior Backend Engineer"
    assert "Build the whole thing" in postings[0]["text"]  # full (escaped) body


def test_prefetch_scrape_samples_exclude_nav_pages() -> None:
    # A plain (non-ATS) careers page: fall back to homepage links, keeping posting
    # slugs and dropping about/team/nav pages (those confuse the generator about
    # where the posting body lives).
    home = (
        '<a href="/about">about</a><a href="/team">team</a>'
        '<a href="/careers/senior-backend-engineer">role</a>'
    )
    pages = {
        "/": home,
        "/robots.txt": "",
        "/sitemap.xml": "",
        "/careers/senior-backend-engineer": "Job body",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://x.com/", ctx)

    assert corpus["apis"] == []
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert {p["url"] for p in postings} == {
        "https://x.com/careers/senior-backend-engineer"
    }
