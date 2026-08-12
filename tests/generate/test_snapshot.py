"""Tests for snapshot/corpus shaping (§4.2, §9).

The generator reasons over a *prefetched* board snapshot injected into the RLM
REPL as a real variable. dspy serializes it as a Python literal and raises on any
non-JSON type, so the corpus must be an addressable dict of JSON primitives
(addressable parts beat one blob — fewer tokens). Pyodide lacks grep/sed, so we
ship plain-Python navigation helpers the prompt references.

The job-board specifics are ATS detection: a host/URL pattern that names a known
ATS (Greenhouse/Lever/Ashby/Gupy/Gem/Rippling/Join/Workable) becomes a candidate
structured-API URL in the corpus, and when one answers during prefetch we sample
real postings and the board's open-role count.
"""

from __future__ import annotations

import ast
import json
from typing import cast

import httpx
import pytest

from jobcrawler.cli_generate import _empty_board_signal
from jobcrawler.generate.snapshot import (
    REPL_HELPERS,
    _ats_postings,
    ats_api_candidates,
    build_corpus,
    count_sitemap_urls,
    ensure_json_primitive,
    on_domain_links,
    pick_jobs_sitemap,
    prefetch_corpus,
    probe_board_empty,
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


def test_ats_api_candidates_gem_api_and_board_urls() -> None:
    # Gem exposes a bare-array API; the token is the path segment after /v0/ on the
    # API host or the first segment on the human jobs.gem.com board.
    gem_api = "https://api.gem.com/job_board/v0/acme/job_posts/"
    assert ats_api_candidates("https://api.gem.com/job_board/v0/acme/job_posts/") == [
        gem_api
    ]
    assert ats_api_candidates("https://jobs.gem.com/acme") == [gem_api]


def test_ats_api_candidates_rippling_board_and_api_urls() -> None:
    # Rippling's human board is ats.rippling.com/<token>/jobs; the canonical API
    # form carries the token after /board/. Both resolve to the same paged endpoint.
    rippling_api = (
        "https://ats.rippling.com/api/v2/board/acme/jobs?page=0&pageSize=1000"
    )
    assert ats_api_candidates("https://ats.rippling.com/acme/jobs") == [rippling_api]
    assert (
        ats_api_candidates(
            "https://ats.rippling.com/api/v2/board/acme/jobs?page=0&pageSize=1000"
        )
        == [rippling_api]
    )


def test_ats_api_candidates_join_numeric_company_id_only() -> None:
    # Join's public API keys off the numeric company id; the human
    # join.com/companies/<slug> board hides it, so only the id form is resolvable.
    assert ats_api_candidates(
        "https://join.com/api/public/companies/117532/jobs?page=1&pageSize=5"
    ) == ["https://join.com/api/public/companies/117532/jobs?page=1&pageSize=5"]
    assert ats_api_candidates("https://join.com/companies/acme") == []


def test_ats_api_candidates_workable_widget_api_ref_and_board() -> None:
    # The registry stores Workable sites as the widget API ref itself
    # (apply.workable.com/api/v1/widget/accounts/<token>), a hosted
    # apply.workable.com/<token> board, or a <token>.workable.com subdomain — all
    # resolve to the ?details=true widget endpoint (details carry the job bodies).
    workable_api = "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    assert ats_api_candidates(
        "https://apply.workable.com/api/v1/widget/accounts/acme"
    ) == [workable_api]
    assert ats_api_candidates("https://apply.workable.com/acme/?lng=en") == [
        workable_api
    ]
    assert ats_api_candidates("https://acme.workable.com/") == [workable_api]
    # The v3 accounts form and hyphenated real tokens (d-id, risingwave-labs) resolve.
    assert ats_api_candidates(
        "https://apply.workable.com/api/v3/accounts/risingwave-labs/jobs"
    ) == [
        "https://apply.workable.com/api/v1/widget/accounts/risingwave-labs?details=true"
    ]


def test_ats_api_candidates_workable_skips_reserved_j_path() -> None:
    # A per-job link apply.workable.com/j/<shortcode> carries no account slug — the
    # /j/ path must not be taken as one; a hosted-board job link still resolves to
    # the account slug that precedes /j/.
    assert ats_api_candidates("https://apply.workable.com/j/97904BAC90") == []
    assert ats_api_candidates("https://apply.workable.com/acme/j/97904BAC90") == [
        "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    ]


def test_ats_api_candidates_empty_on_a_plain_careers_page() -> None:
    # A hand-rolled careers page names no ATS — the generator gets no false candidate.
    assert (
        ats_api_candidates("https://example.com/careers", "<html>Join our team</html>")
        == []
    )


# -- ATS payload parsing (_ats_postings) -------------------------------------
# The count returned is the board's OWN authoritative total, so a populated board
# never reads as empty even when a broken crawler yields nothing (the §6 guard).


def _parsed(data: object) -> tuple[list, int]:
    parsed = _ats_postings(data)
    assert parsed is not None, "expected a recognized ATS payload shape"
    return parsed


def test_ats_postings_rippling_items_with_total() -> None:
    data = {
        "items": [{"name": "Eng"}, {"name": "PM"}],
        "totalItems": 20,
        "page": 0,
        "pageSize": 1000,
        "totalPages": 1,
    }
    postings, total = _parsed(data)
    assert postings == data["items"]
    assert total == 20  # totalItems wins over len(items), the enumeration target


def test_ats_postings_rippling_empty_board_is_zero() -> None:
    postings, total = _parsed({"items": [], "totalItems": 0, "totalPages": 0})
    assert postings == []
    assert total == 0


def test_ats_postings_join_items_with_pagination_rowcount() -> None:
    data = {
        "aggregations": {},
        "items": [{"title": "Eng"}],
        "pagination": {"rowCount": 15, "pageCount": 3, "page": 1, "pageSize": 5},
    }
    postings, total = _parsed(data)
    assert postings == data["items"]
    assert total == 15  # pagination.rowCount is the board's own total


def test_ats_postings_join_empty_board_is_zero() -> None:
    postings, total = _parsed(
        {"items": [], "pagination": {"rowCount": 0, "pageCount": 0, "pageSize": 0}}
    )
    assert postings == []
    assert total == 0


def test_ats_postings_workable_jobs_without_meta_uses_len() -> None:
    # Workable's widget payload is {name, description, jobs:[...]} with no count
    # field — the existing jobs-branch parse returns the jobs and total falls back to
    # len(jobs), the enumeration target.
    data = {
        "name": "Acme",
        "description": "We build things.",
        "jobs": [{"title": "Eng", "url": "u1"}, {"title": "PM", "url": "u2"}],
    }
    postings, total = _parsed(data)
    assert postings == data["jobs"]
    assert total == 2


def test_ats_postings_workable_empty_board_is_zero() -> None:
    postings, total = _parsed({"name": "Acme", "description": "", "jobs": []})
    assert postings == []
    assert total == 0


def test_ats_postings_unrecognized_shape_is_none_not_empty() -> None:
    # A JSON *error* body (Lever's tenant-gone {"ok": false, ...}) parses fine but
    # is not a board saying "zero openings" — None keeps probe_board_empty from
    # parking a retired board as validly empty.
    assert _ats_postings({"ok": False, "error": "Document not found"}) is None
    assert _ats_postings("not a board") is None
    assert _ats_postings(42) is None


# -- authoritative-emptiness probe (§4.8 park-not-heal) ----------------------


def _lever_ctx(api_body: httpx.Response) -> Ctx:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.lever.co":
            return api_body
        return httpx.Response(200, text="<html>board shell</html>")

    return Ctx(
        {"jobs.lever.co", "api.lever.co"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )


def test_probe_board_empty_true_on_empty_lever_array() -> None:
    # The mistral shape: the board exists and answers `[]` — validly empty.
    ctx = _lever_ctx(httpx.Response(200, json=[]))
    assert probe_board_empty(ctx, "https://jobs.lever.co/acme") is True


def test_probe_board_empty_false_when_roles_are_advertised() -> None:
    ctx = _lever_ctx(httpx.Response(200, json=[{"text": "Eng", "hostedUrl": "u"}]))
    assert probe_board_empty(ctx, "https://jobs.lever.co/acme") is False


def test_probe_board_empty_none_when_the_tenant_is_gone() -> None:
    # A retired/moved tenant 404s (ctx.fetch raises on 4xx) — emptiness cannot be
    # established, so the healer keeps the site on the regeneration path.
    ctx = _lever_ctx(httpx.Response(404, json={"ok": False, "error": "not found"}))
    assert probe_board_empty(ctx, "https://jobs.lever.co/acme") is None


def test_probe_board_empty_none_on_a_200_error_stub() -> None:
    # Parseable JSON that is not a recognized board payload must read as
    # "can't tell", never as "empty".
    ctx = _lever_ctx(httpx.Response(200, json={"ok": False, "error": "moved"}))
    assert probe_board_empty(ctx, "https://jobs.lever.co/acme") is None


def test_probe_board_empty_none_without_a_known_ats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>plain careers page</html>")

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    assert probe_board_empty(ctx, "https://x.com/careers") is None


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


def test_prefetch_samples_a_gem_board_bare_array() -> None:
    # Gem returns a bare JSON array (like Lever): detection surfaces the API, the
    # existing array parse grounds the generator in real postings, and the array
    # length is the enumeration target.
    board = [
        {
            "absolute_url": "https://jobs.gem.com/acme/1",
            "title": "Backend Engineer",
            "content": "Build the platform.",
        },
        {
            "absolute_url": "https://jobs.gem.com/acme/2",
            "title": "Frontend Engineer",
            "content": "Ship the UI.",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.gem.com":
            return httpx.Response(200, json=board)
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"jobs.gem.com", "api.gem.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://jobs.gem.com/acme", ctx)

    assert corpus["apis"] == ["https://api.gem.com/job_board/v0/acme/job_posts/"]
    assert corpus["expected_positions"] == 2  # the bare array's length
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert postings[0]["url"] == "https://jobs.gem.com/acme/1"
    assert postings[0]["title"] == "Backend Engineer"
    assert "Build the platform." in postings[0]["text"]


def test_prefetch_admits_an_empty_rippling_board() -> None:
    # A Rippling board that is validly empty right now: the candidate API answers
    # {items:[], totalItems:0}. The corpus still surfaces the API and reports zero
    # open roles — the (apis non-empty AND expected_positions==0) shape the §6
    # empty-board admission keys off, so it is admitted, not failed as under-enumerated.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/board/moov/jobs":
            return httpx.Response(
                200, json={"items": [], "totalItems": 0, "totalPages": 0}
            )
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"ats.rippling.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://ats.rippling.com/moov/jobs", ctx)

    assert corpus["apis"] == [
        "https://ats.rippling.com/api/v2/board/moov/jobs?page=0&pageSize=1000"
    ]
    assert corpus["expected_positions"] == 0
    assert corpus["postings"] == []
    assert _empty_board_signal(corpus) == 0  # §6 admits it as a clean empty board


def test_prefetch_reads_the_rippling_total_even_when_no_samples_extract() -> None:
    # Precision guard: a POPULATED Rippling board whose item keys we don't sample
    # still reports its authoritative totalItems as expected_positions — so a broken
    # 0-yield crawler is never mistaken for an empty board (mirrors Greenhouse
    # meta.total; §6).
    board = {"items": [{"id": "1"}, {"id": "2"}], "totalItems": 12, "totalPages": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/board/moov/jobs":
            return httpx.Response(200, json=board)
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"ats.rippling.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://ats.rippling.com/moov/jobs", ctx)

    assert corpus["expected_positions"] == 12  # totalItems, not the 0 samples
    assert corpus["postings"] == []  # id-only items yield no url/text samples...
    assert _empty_board_signal(corpus) is None  # ...but it is NOT admitted as empty


def test_prefetch_samples_a_workable_widget_board() -> None:
    # Workable's widget returns {name, description, jobs:[...]}; detection surfaces the
    # ?details=true endpoint, the existing jobs-branch parse grounds the generator in
    # real postings, and len(jobs) is the enumeration target (no count field).
    board = {
        "name": "Acme",
        "description": "We build things.",
        "jobs": [
            {
                "url": "https://apply.workable.com/j/AAA",
                "title": "Backend Engineer",
                "description": "Build the platform.",
            },
            {
                "url": "https://apply.workable.com/j/BBB",
                "title": "Frontend Engineer",
                "description": "Ship the UI.",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/widget/accounts/"):
            return httpx.Response(200, json=board)
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"apply.workable.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://apply.workable.com/acme", ctx)

    assert corpus["apis"] == [
        "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"
    ]
    assert corpus["expected_positions"] == 2  # len(jobs), no count field
    postings = cast("list[dict[str, str]]", corpus["postings"])
    assert postings[0]["url"] == "https://apply.workable.com/j/AAA"
    assert postings[0]["title"] == "Backend Engineer"
    assert "Build the platform." in postings[0]["text"]


def test_prefetch_admits_an_empty_workable_board() -> None:
    # d_id/risingwave are validly-empty Workable boards right now: the widget answers
    # {jobs:[]}. The corpus still surfaces the API and reports zero open roles — the
    # (apis non-empty AND expected_positions==0) shape §6 empty-board admission keys
    # off, so it is admitted, not failed as under-enumerated.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/widget/accounts/"):
            return httpx.Response(
                200, json={"name": "D-ID", "description": "", "jobs": []}
            )
        return httpx.Response(200, text="")

    ctx = Ctx(
        {"apply.workable.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus(
        "https://apply.workable.com/api/v1/widget/accounts/d-id", ctx
    )

    assert corpus["apis"] == [
        "https://apply.workable.com/api/v1/widget/accounts/d-id?details=true"
    ]
    assert corpus["expected_positions"] == 0
    assert corpus["postings"] == []
    assert _empty_board_signal(corpus) == 0  # §6 admits it as a clean empty board


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


def test_prefetch_never_drives_a_browser() -> None:
    # Generation is static-only, by operator instruction: an empty JS-shell
    # homepage yields an empty corpus rather than escalating into the operator's
    # Chrome over CDP (which stole window focus on every generated crawler).
    # `ctx.render` stays a declared-but-unbuilt tier, so any future prefetch path
    # that reaches for a browser fails loudly instead of opening one.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body></body></html>")

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )

    corpus = prefetch_corpus("https://x.com/", ctx)

    assert corpus["postings"] == []
    assert corpus["apis"] == []
    assert corpus["expected_positions"] == 0
    with pytest.raises(NotImplementedError):
        ctx.render("https://x.com/")
