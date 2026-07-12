"""Snapshot / corpus shaping for the RLM generator (§4.2, §9).

The generator is kept *idiomatic* RLM by handing it a prefetched board snapshot
as the large input it reasons over in-REPL, using host tools only for follow-up
fetches. dspy injects ``corpus`` into the Pyodide REPL as a real variable by
serializing it as a Python literal, and **raises on any non-JSON type** — so the
corpus is an addressable dict of JSON primitives:

    {homepage, robots, sitemap, apis, feeds, postings: [{url, title, text}, ...]}

Addressable parts (the model slices what it needs) beat one opaque blob — up to
8x fewer tokens. Pyodide ships no grep/sed, so ``REPL_HELPERS`` provides plain
navigation functions the generator prompt tells the model are available.

The job-board specifics live in ATS detection: a host/URL pattern that names a
known ATS (Greenhouse/Lever/Ashby/Gupy) is turned into candidate structured-API
URLs surfaced in ``apis`` — the generator sees *candidates* to probe, never
conclusions. When one answers during prefetch we take real posting samples and
the board's total open-role count (the enumeration target the §6 floor keys off).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from jobcrawler import config
from jobcrawler.urls import normalize_host, valid_on_domain_url

if TYPE_CHECKING:
    from jobcrawler.runtime.contract import Ctx
    from jobcrawler.runtime.render import RenderResult

_JSON_SCALARS = (str, int, float, bool, type(None))
_LOC_RE = re.compile(r"<loc\b", re.IGNORECASE)

# -- ATS detection -----------------------------------------------------------
# A known ATS exposes the WHOLE board as structured JSON — the anti-under-
# enumeration source. Detection is host/URL-pattern first (the board token lives
# in the URL) with a homepage-embed fallback (a company careers page iframing or
# linking its ATS board). Empirical endpoints (live probes 2026-07-02):
#   Greenhouse: boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
#   Lever:      api.lever.co/v0/postings/<token>?mode=json
#   Ashby:      api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true
#   Gupy (BR):  employability-portal.gupy.io/api/v1/jobs?companyId=..&limit=100
#               (companyId lives in the board's __NEXT_DATA__, not the URL)
# Empirical endpoints (live probes 2026-07-12):
#   Gem:      api.gem.com/job_board/v0/<token>/job_posts/ (bare array, like Lever)
#   Rippling: ats.rippling.com/api/v2/board/<token>/jobs  ({items, totalItems})
#   Join:     join.com/api/public/companies/<id>/jobs     ({items, pagination.
#             rowCount}; <id> is the numeric company id, not a slug)
#   Workable: apply.workable.com/api/v1/widget/accounts/<token>?details=true
#             ({name, description, jobs:[...]}; ?details=true is REQUIRED for the
#             job objects to carry descriptions + apply URLs; no count field, so
#             the §6 total is len(jobs))
_ATS_TOKEN = r"([A-Za-z0-9][A-Za-z0-9._-]*)"
_GH_FOR = re.compile(
    r"greenhouse\.io/embed/job_board\?(?:[^\"'>\s]*&)?for=" + _ATS_TOKEN, re.IGNORECASE
)
_GH_API_REF = re.compile(
    r"boards-api\.greenhouse\.io/v1/boards/" + _ATS_TOKEN, re.IGNORECASE
)
_GH_BOARD = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/" + _ATS_TOKEN, re.IGNORECASE
)
_LEVER_REF = re.compile(
    r"(?:jobs\.lever\.co|api\.lever\.co/v0/postings)/" + _ATS_TOKEN, re.IGNORECASE
)
_ASHBY_REF = re.compile(
    r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/" + _ATS_TOKEN,
    re.IGNORECASE,
)
_GUPY_REF = re.compile(r"\bgupy\.io\b", re.IGNORECASE)
# Gem — bare JSON array (parsed like Lever); the board token is the path segment
# after ``/v0/`` on the API host or the first segment on the human board hosts.
_GEM_API_REF = re.compile(
    r"api\.gem\.com/job_board/v0/" + _ATS_TOKEN + r"/job_posts", re.IGNORECASE
)
_GEM_BOARD = re.compile(r"(?:jobs|job-boards)\.gem\.com/" + _ATS_TOKEN, re.IGNORECASE)
# Rippling — ``{items, totalItems}``; token after ``/board/`` on the API ref, else
# the first path segment before ``/jobs`` on the human/embed board.
_RIPPLING_API_REF = re.compile(
    r"ats\.rippling\.com/api/v2/board/" + _ATS_TOKEN + r"/jobs", re.IGNORECASE
)
_RIPPLING_BOARD = re.compile(
    r"ats\.rippling\.com/(?:embed/)?" + _ATS_TOKEN + r"/jobs", re.IGNORECASE
)
# Join — ``{items, pagination.rowCount}``; only the numeric-company-id API form is
# resolvable (the human ``join.com/companies/<slug>`` board hides the id).
_JOIN_API_REF = re.compile(r"join\.com/api/public/companies/(\d+)/jobs", re.IGNORECASE)
# Workable — GET widget ``{name, description, jobs:[...]}``. The account slug comes
# from the widget API ref (unambiguous), a ``<slug>.workable.com`` subdomain board,
# or a hosted ``apply.workable.com/<slug>`` board; Workable's own reserved
# subdomains/paths (``apply``/``api``/``j`` …) are skipped so they're never the slug.
_WORKABLE_API_REF = re.compile(
    r"apply\.workable\.com/api/v\d+/(?:widget/)?accounts/" + _ATS_TOKEN, re.IGNORECASE
)
_WORKABLE_SUBDOMAIN = re.compile(
    r"\b([A-Za-z0-9][A-Za-z0-9_-]*)\.workable\.com", re.IGNORECASE
)
_WORKABLE_BOARD = re.compile(r"apply\.workable\.com/" + _ATS_TOKEN, re.IGNORECASE)
_WORKABLE_RESERVED = frozenset(
    {"www", "apply", "api", "help", "assets", "cdn", "static", "j", "widget",
     "accounts", "jobs"}
)

_GH_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
_LEVER_API = "https://api.lever.co/v0/postings/{token}?mode=json"
_ASHBY_API = (
    "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
)
_GUPY_API = "https://employability-portal.gupy.io/api/v1/jobs?limit=100"
_GEM_API = "https://api.gem.com/job_board/v0/{token}/job_posts/"
_RIPPLING_API = (
    "https://ats.rippling.com/api/v2/board/{token}/jobs?page=0&pageSize=1000"
)
_JOIN_API = "https://join.com/api/public/companies/{token}/jobs?page=1&pageSize=5"
_WORKABLE_API = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"

# Posting-shape keys probed tolerantly across the ATS JSON dialects for samples.
_ATS_URL_KEYS = (
    "absolute_url",
    "hostedUrl",
    "jobUrl",
    "applyUrl",
    "applicationUrl",
    "url",
)
_ATS_TITLE_KEYS = ("title", "name", "text")
_ATS_TEXT_KEYS = ("content", "descriptionPlain", "descriptionHtml", "description")

# Path segments on a plain careers page that are never a posting (nav, marketing,
# auth, legal) — excluded from the scrape-fallback samples so the generator isn't
# shown an about/benefits page as if it were a job. ``vaga``/``vagas`` (pt-BR for
# "job") is deliberately NOT excluded — that is where postings live.
_NON_POSTING = re.compile(
    r"/(about|sobre|contact|contato|team|equipe|time|blog|news|noticias?|press|"
    r"imprensa|privacy|privacidade|terms|termos|cookies?|login|sign-?in|signup|"
    r"sign-?up|entrar|cadastr\w*|benefits?|beneficios?|culture|cultura|faq|"
    r"perguntas|help|ajuda|home|inicio)(/|$|\?)",
    re.IGNORECASE,
)


def build_corpus(
    *,
    homepage: str,
    robots: str = "",
    sitemap: str = "",
    sitemaps: Iterable[str] | None = None,
    expected_positions: int = 0,
    postings: Iterable[Mapping[str, object]] | None = None,
    feeds: Iterable[str] | None = None,
    apis: Iterable[str] | None = None,
) -> dict[str, object]:
    """Assemble the prefetched snapshot as an addressable, JSON-able dict (§9).

    ``feeds`` carries the discovered RSS/Atom/JSON feed URLs — the feed-first
    signal the strategy reasons over — so the model needn't rediscover them.
    ``apis`` carries the candidate structured ATS API URLs (Greenhouse/Lever/
    Ashby/Gupy/Gem/Rippling/Join) that enumerate the *whole* board as JSON — the
    best source when present. ``sitemaps`` are the sitemap URLs advertised in robots and
    ``expected_positions`` is the host's estimate of how many open roles the board
    has (the ATS total when an API answered, else the jobs-sitemap count) — the
    enumeration target the generator reasons toward so it doesn't settle for a
    feed-sized sample (§4.2, §6).
    """
    corpus: dict[str, object] = {
        "homepage": homepage,
        "robots": robots,
        "sitemap": sitemap,
        "sitemaps": [str(s) for s in (sitemaps or [])],
        "expected_positions": int(expected_positions),
        "apis": [str(a) for a in (apis or [])],
        "postings": [_posting(p) for p in (postings or [])],
        "feeds": [str(f) for f in (feeds or [])],
    }
    ensure_json_primitive(corpus)
    return corpus


def ats_api_candidates(site_url: str, homepage: str = "") -> list[str]:
    """Candidate structured ATS JSON API URLs for ``site_url`` (empty if none).

    Detects a known ATS from the board URL itself or from a homepage embed (a
    company careers page iframing/linking its ATS board), and turns the extracted
    board token into the empirical enumerate-the-whole-board endpoint. These are
    *candidates* the generator probes and verifies — never conclusions. Gupy is
    surfaced by its portal endpoint even though the ``companyId`` lives in the
    board's ``__NEXT_DATA__`` (the generator fills it in).
    """
    haystack = f"{site_url}\n{homepage}"
    out: list[str] = []

    def add(url: str) -> None:
        if url not in out:
            out.append(url)

    token = _greenhouse_token(haystack)
    if token:
        add(_GH_API.format(token=token))
    match = _LEVER_REF.search(haystack)
    if match:
        add(_LEVER_API.format(token=match.group(1)))
    match = _ASHBY_REF.search(haystack)
    if match:
        add(_ASHBY_API.format(token=match.group(1)))
    if _GUPY_REF.search(haystack):
        add(_GUPY_API)
    match = _GEM_API_REF.search(haystack) or _GEM_BOARD.search(haystack)
    if match:
        add(_GEM_API.format(token=match.group(1)))
    match = _RIPPLING_API_REF.search(haystack) or _RIPPLING_BOARD.search(haystack)
    if match:
        add(_RIPPLING_API.format(token=match.group(1)))
    match = _JOIN_API_REF.search(haystack)
    if match:
        add(_JOIN_API.format(token=match.group(1)))
    token = _workable_token(haystack)
    if token:
        add(_WORKABLE_API.format(token=token))
    return out


def _greenhouse_token(haystack: str) -> str | None:
    """The Greenhouse board token from an embed ``for=``, an API ref, or a board
    path — preferring the unambiguous forms so ``/embed/job_board`` isn't mistaken
    for the token."""
    for rx in (_GH_FOR, _GH_API_REF):
        match = rx.search(haystack)
        if match:
            return match.group(1)
    for match in _GH_BOARD.finditer(haystack):
        if match.group(1).lower() not in ("embed", "job_board"):
            return match.group(1)
    return None


def _workable_token(haystack: str) -> str | None:
    """The Workable account slug — from the widget API ref (unambiguous), a
    ``<slug>.workable.com`` subdomain board, or a hosted ``apply.workable.com/<slug>``
    board — skipping Workable's own reserved subdomains/paths so ``apply``/``api``/
    ``j`` are never mistaken for the slug."""
    match = _WORKABLE_API_REF.search(haystack)
    if match:
        return match.group(1)
    for rx in (_WORKABLE_SUBDOMAIN, _WORKABLE_BOARD):
        for match in rx.finditer(haystack):
            token = match.group(1)
            if token.lower() not in _WORKABLE_RESERVED:
                return token
    return None


def _ats_posting_samples(
    ctx: Ctx, api_url: str, count: int, clip_chars: int
) -> tuple[list[dict[str, str]], int]:
    """Real posting samples + the board's total open-role count from an ATS API.

    Tolerant across the ATS JSON dialects (Greenhouse ``{meta, jobs}``, Lever's
    bare array, Ashby ``{jobs}``): the samples ground the generator in the real
    shape and the count is the enumeration target the §6 floor keys off.
    Best-effort — a non-answering or unparseable endpoint yields ``([], 0)``.
    """
    raw = _try_fetch(ctx, api_url)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return [], 0
    postings, total = _ats_postings(data)
    out: list[dict[str, str]] = []
    for posting in postings[:count]:
        if not isinstance(posting, Mapping):
            continue
        url = _ats_field(posting, _ATS_URL_KEYS)
        text = _ats_field(posting, _ATS_TEXT_KEYS)
        if url and text:
            out.append(
                {
                    "url": url,
                    "title": _ats_field(posting, _ATS_TITLE_KEYS),
                    "text": _clip(text, clip_chars),
                }
            )
    return out, total


def _ats_postings(data: object) -> tuple[list, int]:
    """The postings list + total from a parsed ATS payload.

    Lever/Gem are bare arrays; Greenhouse/Ashby wrap them under ``jobs`` (with a
    ``meta.total`` for Greenhouse); Rippling/Join wrap them under ``items`` with an
    authoritative count in ``totalItems`` (Rippling) / ``pagination.rowCount``
    (Join). The count is the board's OWN total, so a populated board never reads as
    empty even when a broken crawler yields nothing (the §6 precision guard)."""
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, Mapping):
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            meta = data.get("meta")
            total = meta.get("total") if isinstance(meta, Mapping) else None
            return jobs, total if isinstance(total, int) and total > 0 else len(jobs)
        items = data.get("items")
        if isinstance(items, list):
            total = data.get("totalItems")
            if not isinstance(total, int):
                pagination = data.get("pagination")
                total = (
                    pagination.get("rowCount")
                    if isinstance(pagination, Mapping)
                    else None
                )
            return items, total if isinstance(total, int) and total > 0 else len(items)
    return [], 0


def _ats_field(mapping: Mapping[object, object], keys: tuple[str, ...]) -> str:
    """The first non-empty string value among ``keys`` (ATS dialects vary)."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _looks_like_posting(url: str) -> bool:
    """A homepage link that plausibly points at one job posting (not nav/marketing).

    Excludes obvious careers-page chrome, then keeps only links whose final path
    segment reads like a posting *slug* — multi-word/hyphenated, an id with digits,
    or long — so a bare section landing page (``/careers/``, ``/vagas/``) is not
    sampled as a posting.
    """
    if _NON_POSTING.search(url):
        return False
    slug = urlparse(url).path.strip("/").rsplit("/", 1)[-1]
    if not slug:
        return False
    return "-" in slug or len(slug) > 20 or any(ch.isdigit() for ch in slug)


def sitemaps_from_robots(robots: str) -> list[str]:
    """The ``Sitemap:`` URLs advertised in robots.txt (§4.2 enumeration discovery)."""
    out: list[str] = []
    for line in robots.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "sitemap" and value.strip():
            out.append(value.strip())
    return out


def pick_jobs_sitemap(sitemaps: list[str]) -> str | None:
    """Prefer a *jobs* sitemap — it enumerates the open roles, the count we want."""
    for url in sitemaps:
        low = url.lower()
        if any(marker in low for marker in ("job", "career", "vaga", "position")):
            return url
    return sitemaps[0] if sitemaps else None


def count_sitemap_urls(sitemap_xml: str) -> int:
    """Count posting URL entries in a (jobs) sitemap; 0 for an index or empty.

    A flat jobs sitemap lists one ``<loc>`` per open role — the natural estimate
    of how many postings the board currently has. A ``<sitemapindex>`` points to
    child sitemaps, not postings, so it yields no estimate here (the enumeration
    gate then stays dormant rather than keying off a wrong, tiny count).
    """
    if "<sitemapindex" in sitemap_xml.lower():
        return 0
    return len(_LOC_RE.findall(sitemap_xml))


def _posting(posting: Mapping[str, object]) -> dict[str, str]:
    return {
        "url": str(posting.get("url") or ""),
        "title": str(posting.get("title") or ""),
        "text": str(posting.get("text") or ""),
    }


def ensure_json_primitive(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is built only of JSON primitives.

    Mirrors dspy's in-sandbox serializer (which raises ``CodeInterpreterError``
    on a non-JSON type), so a bad corpus fails here with a clear message rather
    than deep inside the RLM REPL bridge.
    """
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"corpus dict key must be str, got {type(k).__name__}")
            ensure_json_primitive(v)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            ensure_json_primitive(item)
        return
    raise ValueError(f"corpus holds a non-JSON type: {type(value).__name__}")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(v for k, v in attrs if k == "href" and v)


def on_domain_links(html: str, base_url: str) -> list[str]:
    """On-domain absolute posting-ish links from ``html`` (deduped, base excluded)."""
    allowed = {normalize_host(base_url)}
    parser = _LinkParser()
    parser.feed(html)
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        url = urljoin(base_url, href)
        if url != base_url and url not in seen and valid_on_domain_url(url, allowed):
            seen.add(url)
            out.append(url)
    return out


def prefetch_corpus(
    site_url: str,
    ctx: Ctx,
    *,
    max_postings: int = 5,
    homepage_chars: int | None = None,
    posting_chars: int | None = None,
) -> dict[str, object]:
    """Fetch a board's snapshot through ``ctx`` into the addressable corpus (§4.2).

    The homepage drives discovery; robots/sitemap are best-effort (missing ones
    become empty). When a known ATS API answers, real postings are sampled from it
    and its total open-role count becomes ``expected_positions``; otherwise a few
    on-domain posting-slug links are pulled as samples. All fetching goes through
    ``ctx`` so SSRF + politeness apply. Fetched HTML is **clipped** (§9) — raw
    pages run to 100s of KB, and an unbounded corpus blows past the model's
    context so a reasoning LM exhausts its tokens on nothing. Link discovery uses
    the *full* homepage, so clipping never costs a sample.
    """
    from jobcrawler.generate.tools import find_feeds_in  # avoid an import cycle

    hp_cap = (
        config.corpus_homepage_chars() if homepage_chars is None else homepage_chars
    )
    post_cap = config.corpus_article_chars() if posting_chars is None else posting_chars

    try:
        homepage = ctx.fetch(site_url)
    except Exception as exc:
        # An entrypoint that refuses a bare GET — an API index wanting auth headers
        # (idealist's Algolia root 403s without its search key), a bot wall, a JS
        # shell behind a challenge — is a *fact about the site*, not a reason to
        # abort generation: the operator hints carry the recipe for exactly these
        # boards, and the §6 gate still proves whatever the model writes against the
        # live site. Surface the refusal in-band where the model reads first, so it
        # knows the naked fetch is off the table and leans on hints/probes instead.
        homepage = f"[unfetchable: GET {site_url} failed: {type(exc).__name__}: {exc}]"
    robots = _try_fetch(ctx, urljoin(site_url, "/robots.txt"))
    # Discover a jobs sitemap from robots (it enumerates the open roles); fall back
    # to /sitemap.xml when robots advertises none, so the count is best-effort.
    sitemaps = sitemaps_from_robots(robots)
    sitemap_url = pick_jobs_sitemap(sitemaps) or urljoin(site_url, "/sitemap.xml")
    sitemap = _try_fetch(ctx, sitemap_url)
    # A known ATS (Greenhouse/Lever/Ashby/Gupy) exposes the whole board as JSON —
    # detected from the board URL or a homepage embed. Surface the candidate API
    # URLs so the generator can probe them, and when one answers, take real posting
    # samples + the board's total (the enumeration target the §6 floor keys off).
    apis = ats_api_candidates(site_url, homepage)
    postings, ats_total = _sample_from_apis(ctx, apis, max_postings, post_cap)
    if not postings and not ats_total:
        postings = _scrape_posting_samples(
            ctx, homepage, site_url, max_postings, post_cap
        )
    feeds = find_feeds_in(homepage, site_url)  # feed discovery (§4.2)
    sitemap_count = count_sitemap_urls(sitemap)
    corpus_homepage = homepage

    # §4.10 empty-shell escalation: a static fetch of a JS SPA yields nothing to
    # ground generation on — no postings, no ATS, no jobs-sitemap count, no feed.
    # When a render bridge is *provisioned* (generation opts in; never lazily —
    # see Ctx.render_available), render the shell in a real browser and re-run
    # discovery on the settled DOM plus the runtime resource URLs the page fetched
    # — the XHR/JSON API a static crawler can hit directly.
    if (
        not postings
        and not ats_total
        and not sitemap_count
        and not feeds
        and getattr(ctx, "render_available", False)
    ):
        rendered = _render_shell(ctx, site_url)
        if rendered is not None and rendered.html:
            corpus_homepage = rendered.html
            apis = _dedup(
                [
                    *apis,
                    *ats_api_candidates(site_url, rendered.html),
                    *_api_like(rendered.resource_urls),
                ]
            )
            postings, ats_total = _sample_from_apis(ctx, apis, max_postings, post_cap)
            if not postings and not ats_total:
                postings = _scrape_posting_samples(
                    ctx, rendered.html, site_url, max_postings, post_cap
                )
            feeds = feeds or find_feeds_in(rendered.html, site_url)

    return build_corpus(
        homepage=_clip(corpus_homepage, hp_cap),
        robots=robots,
        sitemap=sitemap,
        sitemaps=sitemaps or ([sitemap_url] if sitemap else []),
        expected_positions=ats_total or sitemap_count,
        apis=apis,
        postings=postings,
        feeds=feeds,
    )


def _sample_from_apis(
    ctx: Ctx, apis: list[str], max_postings: int, post_cap: int
) -> tuple[list[dict[str, str]], int]:
    """First ATS candidate that answers with samples or a total wins (§4.2)."""
    for api in apis:
        samples, total = _ats_posting_samples(ctx, api, max_postings, post_cap)
        if samples or total:
            return samples, total
    return [], 0


def _scrape_posting_samples(
    ctx: Ctx, homepage: str, site_url: str, max_postings: int, post_cap: int
) -> list[dict[str, str]]:
    """Sample posting-slug links from a page (skipping nav/marketing pages, which
    confuse the generator about where the body is) — the no-ATS fallback (§4.2)."""
    links = [
        url for url in on_domain_links(homepage, site_url) if _looks_like_posting(url)
    ][:max_postings]
    return [
        {"url": url, "title": "", "text": _clip(_try_fetch(ctx, url), post_cap)}
        for url in links
    ]


def _render_shell(ctx: Ctx, site_url: str) -> "RenderResult | None":
    """Render the board's empty shell for §4.10 escalation; ``None`` if it can't.

    Best-effort by construction: Chrome may be down, CDP may error, the URL may
    be refused — any of which means "couldn't escalate", and generation proceeds
    on the empty static corpus exactly as it did before the tier existed.
    ``render_observed`` is a runtime-Ctx method (off the crawler contract), so it
    is reached via ``getattr`` — a stub ctx without it simply can't escalate.
    """
    render_observed = getattr(ctx, "render_observed", None)
    if render_observed is None:
        return None
    try:
        return render_observed(site_url)
    except Exception:  # noqa: BLE001 - CdpError / SSRF / unreachable browser
        return None


# Resource URLs worth probing as a jobs API: a rendered SPA's resource list is
# mostly assets (js/css/fonts/images), so only these data-endpoint shapes are
# fetched — keeping the page budget off the noise.
_API_MARKERS = ("/api/", ".json", "graphql")


def _api_like(urls: Iterable[str]) -> list[str]:
    return [u for u in urls if any(marker in u.lower() for marker in _API_MARKERS)]


def _dedup(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _clip(text: str, limit: int) -> str:
    return text if limit <= 0 or len(text) <= limit else text[:limit]


def _try_fetch(ctx: Ctx, url: str) -> str:
    try:
        return ctx.fetch(url)
    except Exception:
        return ""  # robots/sitemap/api/posting fetch is best-effort


# Navigation helpers the generator prompt advertises (Pyodide lacks grep/sed).
# Plain Python so the model can call them on corpus parts inside the REPL.
REPL_HELPERS = '''
def grep(pattern, text, *, flags=0):
    """Return lines of `text` matching the regex `pattern`."""
    import re
    rx = re.compile(pattern, flags)
    return [line for line in text.splitlines() if rx.search(line)]


def slice_text(text, start, end):
    """Return `text[start:end]` — a window into a large corpus part."""
    return text[start:end]
'''
