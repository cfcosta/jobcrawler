"""Host tools for the RLM generator (§4.2).

The tools run on the *host* (the Pyodide sandbox has no network) and handle
follow-up fetches the prefetched corpus doesn't cover. This module holds the
pure, parser-side tool logic; the network/sandbox/Playwright bindings
(``fetch``/``render``/``get_sitemap``/``validate_crawler``) attach in the §4.4
sandbox + §8 hardening work, since feed/sitemap parsing consumes
attacker-influenced markup on the hot path.

``find_feeds_in`` is the HTML discovery primitive: ``<link rel="alternate">`` RSS
/ Atom / JSON feeds, resolved to absolute URLs. ``ats_api_candidates`` is its
job-board sibling: the documented whole-board JSON endpoints of the handful of
ATS platforms most careers pages run on (Greenhouse / Lever / Ashby / Gupy),
derived from the host. Both surface *enumerate-the-whole-board* sources — a feed
or a board API lists every open posting, whereas scraping a paginated HTML
listing only samples a window — so together they are what gate-2(a) must-observe
pushes the generator to consult before falling back to ``scrape`` (now covering
job-board APIs, not just RSS).

The §4.4 sandbox binding (``validate_crawler``) is imported lazily inside the
tool so importing this module for the pure discovery helpers — the §4.2 corpus
build calls ``find_feeds_in`` on the hot path — does not drag in the
subprocess-sandbox machinery.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from jobcrawler.runtime.contract import Ctx
from jobcrawler.runtime.ssrf import SsrfError, check_url
from jobcrawler.urls import host_on_domain, normalize_host

if TYPE_CHECKING:  # type-only: keeps this module import-light (no scoring at runtime)
    from jobcrawler.generate.scoring import CandidateResult

_FEED_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/json",
        "application/feed+json",
    }
)


class _FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        rel = attr.get("rel", "").lower()
        typ = attr.get("type", "").lower()
        href = attr.get("href")
        if href and "alternate" in rel.split() and typ in _FEED_TYPES:
            self.hrefs.append(href)


def _ordered_unique(urls: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication (a page declares the same feed twice)."""
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def find_feeds_in(html: str, base_url: str) -> list[str]:
    """Discover ``<link rel="alternate">`` feeds in ``html`` as absolute URLs.

    Order-preserving and de-duplicated. A feed enumerates every posting; scraping
    a homepage only samples the "recent" window — which is why feed-first
    discovery is the §6 gate-2(a) discipline.
    """
    parser = _FeedLinkParser()
    parser.feed(html)
    return _ordered_unique(urljoin(base_url, href) for href in parser.hrefs)


# -- known-ATS whole-board API discovery (§4.2) ------------------------------
#
# Empirically probed 2026-07-02: each endpoint returns the ENTIRE board in one
# (or a cleanly paginated) call, so surfacing them lets the generator prefer an
# enumerate-the-board API over scraping a paginated HTML listing — and lets
# gate-2(a)'s "consult discovery before scraping" cover API boards, not just RSS.
# Every URL is a *candidate*: the crawler still fetches and validates it, so a
# wrong guess simply 404s rather than fabricating anything.


def _greenhouse_api(token: str) -> str:
    # boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true — the whole
    # board in one call (meta.total); content is the FULL, HTML-entity-escaped
    # description (html.unescape then strip tags).
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def _lever_api(token: str) -> str:
    # api.lever.co/v0/postings/<token>?mode=json — full text is description +
    # lists[].content + additional; workplaceType is always present.
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def _ashby_api(token: str) -> str:
    # api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true —
    # full descriptionPlain + structured compensation ~70% of the time.
    return (
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{token}?includeCompensation=true"
    )


def _gupy_board(token: str) -> str:
    # <token>.gupy.io embeds every job in __NEXT_DATA__ (the employability-portal
    # API paginates the same data) — the board page is the single fetch to parse.
    return f"https://{token}.gupy.io/"


# Careers-y subdomain labels stripped before guessing an org slug from a company
# host (``careers.acme.com`` / ``jobs.acme.com`` both -> ``acme``).
_SUBDOMAIN_NOISE = frozenset(
    {
        "www",
        "careers",
        "career",
        "jobs",
        "job",
        "boards",
        "board",
        "job-boards",
        "apply",
        "work",
        "hiring",
        "talent",
        "recruiting",
        "join",
    }
)


def _first_path_segment(path: str) -> str:
    for segment in path.split("/"):
        if segment:
            return segment
    return ""


def _subdomain_label(host: str, suffix: str) -> str:
    """The single sub-label of ``host`` under ``suffix`` (``acme.gupy.io`` -> ``acme``).

    Empty when ``host`` is the bare suffix or carries more than one sub-label — we
    only trust an unambiguous ``<token>.<suffix>`` vhost as a board token.
    """
    dotted = "." + suffix
    if not host.endswith(dotted):
        return ""
    label = host[: -len(dotted)]
    return label if label and "." not in label else ""


def _org_slug(host: str) -> str:
    """A best-effort org slug from a company/careers host (``careers.acme.com`` -> ``acme``).

    No public-suffix list is shipped, so this approximates: drop a leading
    careers-y subdomain, then take the registrable label. It only ever feeds
    *candidate* endpoints the generator probes, never an assertion — so a rough
    guess is fine (a wrong slug just 404s). Empty for a bare/loopback/numeric host.
    """
    labels = [label for label in host.split(".") if label]
    while len(labels) > 2 and labels[0] in _SUBDOMAIN_NOISE:
        labels = labels[1:]
    if len(labels) < 2:
        return ""
    slug = labels[0]
    return "" if slug.isdigit() else slug


def ats_api_candidates(base_url: str) -> list[str]:
    """Candidate whole-board ATS API endpoints for ``base_url``'s host (§4.2).

    Most job boards run on a few ATS platforms whose documented JSON endpoints
    return the entire board at once (Greenhouse / Lever / Ashby) or embed it in
    the board page (Gupy) — the enumerate-everything sources the §6 gates push the
    generator toward over scraping a paginated listing. When the URL already *is*
    a known ATS board host the platform is known, so we emit its single exact
    endpoint; for a company's own careers domain the platform is unknown, so we
    guess the org slug and offer one candidate per platform to probe. A host with
    no derivable slug yields ``[]``.
    """
    parts = urlsplit(base_url.strip())
    host = (parts.hostname or "").lower()
    if not host:
        return []
    path_token = _first_path_segment(parts.path)

    # Mode A — the URL already IS an ATS board host: the platform is known, so emit
    # the single high-signal endpoint. The token lives in the path for the
    # path-style boards (boards.greenhouse.io/<t>, jobs.lever.co/<t>,
    # jobs.ashbyhq.com/<t>) and in the subdomain for the vhost-style ones.
    if host.endswith("greenhouse.io"):
        token = path_token or _subdomain_label(host, "greenhouse.io")
        return [_greenhouse_api(token)] if token else []
    if host.endswith("lever.co"):
        token = path_token or _subdomain_label(host, "lever.co")
        return [_lever_api(token)] if token else []
    if host.endswith("ashbyhq.com"):
        token = path_token or _subdomain_label(host, "ashbyhq.com")
        return [_ashby_api(token)] if token else []
    if host.endswith("gupy.io"):
        token = _subdomain_label(host, "gupy.io")
        return [_gupy_board(token)] if token else []

    # Mode B — a company/careers domain: the platform is unknown, so guess the org
    # slug and offer one candidate per platform (the generator probes; wrong 404).
    slug = _org_slug(host)
    if not slug:
        return []
    return [
        _greenhouse_api(slug),
        _lever_api(slug),
        _ashby_api(slug),
        _gupy_board(slug),
    ]


# -- observed-host evidence (§8) ----------------------------------------------

# Literal ``https://<host>`` tokens in served bytes — the direct evidence channel.
_HOST_LITERAL_RE = re.compile(
    r"https?://([A-Za-z0-9][A-Za-z0-9.-]{1,251}[A-Za-z0-9])", re.ASCII
)

# Board-platform host map (§8): a site built on one of these platforms loads its
# postings from a documented API host the served HTML may never name *literally* —
# the client constructs it in JS. Observed empirically on Getro boards
# (jobs.madrona.com / jobs.solana.com): the HTML names cdn.getro.com assets 88×
# while the postings ride api.getro.com, which appears 0 times. Purely mechanical,
# TCB-owned knowledge — the cross-host sibling of ``ats_api_candidates``.
_PLATFORM_API_HOSTS: tuple[tuple[str, str], ...] = (("getro.com", "api.getro.com"),)


def observed_hosts(texts: Iterable[str], base_url: str) -> list[str]:
    """Hosts the site's own served bytes vouch for — the §8 EXTRA_HOSTS evidence.

    Two channels, both computed by trusted host code over the prefetched corpus,
    never by the model: literal ``https://<host>`` tokens in ``texts``, and the
    platform map above (plus the known-ATS API hosts of
    :func:`ats_api_candidates`). The site's own host and its subdomains are
    excluded — they are already the allowlist, not "extra" — and so is anything
    the fixed SSRF kernel denies: a page that names ``metadata.google.internal``
    never turns that into evidence. Platform-implied hosts sort first (they carry
    the strongest signal; callers bound how many candidates they probe), then
    literals alphabetically.

    This is *evidence*, not authorization: gate-1(b) admits a declared host only
    against this set, and the fixed runtime enforcement (SSRF kernel per hop,
    robots, pacing, page budget) applies to every host regardless of provenance.
    """
    base_host = normalize_host(base_url)
    literals: set[str] = set()
    for text in texts:
        literals.update(h.lower().strip(".") for h in _HOST_LITERAL_RE.findall(text))
    platform = {
        api
        for suffix, api in _PLATFORM_API_HOSTS
        if any(h == suffix or h.endswith("." + suffix) for h in literals)
    }
    platform.update(
        normalize_host(candidate) for candidate in ats_api_candidates(base_url)
    )

    def eligible(host: str) -> bool:
        if not host or "." not in host:
            return False
        if base_host and host_on_domain(host, (base_host,)):
            return False  # already the allowlist; not "extra"
        try:
            check_url(f"https://{host}/", {host})
        except SsrfError:
            return False  # kernel-denied names are never evidence
        return True

    ranked = sorted(h for h in platform if eligible(h))
    ranked += sorted(h for h in literals - platform if eligible(h))
    return ranked


def corpus_host_evidence(corpus: object, base_url: str) -> list[str]:
    """The §8 EXTRA_HOSTS evidence for a site, from its prefetched corpus (§4.2).

    Walks the JSON-primitive corpus collecting every string it serves (keys too —
    the ``apis`` map is keyed by URL) and hands them to :func:`observed_hosts`.
    The one entry point generation and optimize share, so the evidence rule can't
    drift between them.
    """
    texts: list[str] = []
    _collect_strings(corpus, texts)
    return observed_hosts(texts, base_url)


def _collect_strings(node: object, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            _collect_strings(key, out)
            _collect_strings(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_strings(item, out)


# Per-sample description head shown to the model. Presentation only: the §6 gate
# and §4.11 axes grade the sandbox's full (pipe-bounded) text — this clip exists so
# five samples fit the RLM bridge's 10k-char output budget (§4.3) instead of being
# head+tail-spliced into unparseable JSON. The marker keeps the model from reading
# a preview as a short (or cross-sample identical) extraction.
_DESCRIPTION_PREVIEW = 600


def _preview_sample(sample: dict) -> dict:
    """Clip one sample's description for the RLM-facing ``validate_crawler`` dict."""
    description = str(sample.get("description") or "")
    if len(description) <= _DESCRIPTION_PREVIEW:
        return sample
    hidden = len(description) - _DESCRIPTION_PREVIEW
    return {
        **sample,
        "description": description[:_DESCRIPTION_PREVIEW]
        + f" …[preview clipped; +{hidden} extracted chars are graded in full]",
    }


def _sandbox_validate() -> Callable[..., CandidateResult]:
    """Bind the §4.4 sandbox validator lazily (keeps this module import-light).

    Deferred to first use so importing ``tools`` for the pure discovery helpers
    never drags in the subprocess-sandbox machinery, and so a caller can inject a
    fake instead (``make_host_tools(..., validate_fn=...)``) — the same "inject the
    scoring core so the wiring is testable without a live sandbox" idiom scoring.py
    uses.
    """
    from jobcrawler.runtime.sandbox import validate_crawler

    return validate_crawler


def make_host_tools(
    ctx: Ctx,
    allowed_hosts: Collection[str],
    *,
    validate_timeout: float = 10.0,
    validate_fn: Callable[..., CandidateResult] | None = None,
) -> list[Callable[..., object]]:
    """The §4.2 host tools handed to ``dspy.RLM(tools=...)``.

    They run on the host (the Pyodide sandbox has no network or crawler libs):
    ``fetch``/``find_feeds``/``get_sitemap`` cover follow-up fetches the prefetched
    corpus missed, and ``validate_crawler`` is the bridge to the §4.4 execution
    sandbox so the model can write → validate → fix before SUBMIT. Each is a plain
    named callable with a docstring (RLM uses these as the tool descriptions).

    ``validate_fn`` overrides the §4.4 sandbox validator (default: bound lazily) so
    the reshaping is testable without spawning the subprocess.
    """
    hosts = set(allowed_hosts)

    def fetch(url: str, method: str = "GET", payload: str = "") -> str:
        """Fetch an allowed URL and return its text. For a JSON API that only
        answers POST (a board search endpoint), pass method="POST" and the JSON
        body as `payload` (a JSON object string)."""
        body = json.loads(payload) if payload.strip() else None
        return ctx.fetch(url, method=method, json=body)

    def find_feeds(url: str) -> list[str]:
        """Discovery candidates for `url`: RSS/Atom/JSON feeds linked from the page,
        plus known-ATS whole-board API endpoints (Greenhouse/Lever/Ashby/Gupy) for
        the host — the enumerate-every-posting sources to prefer over scraping."""
        feeds = find_feeds_in(ctx.fetch(url), url)
        return _ordered_unique([*feeds, *ats_api_candidates(url)])

    def get_sitemap(url: str) -> str:
        """Fetch the sitemap XML at `url` (e.g. https://site/sitemap.xml)."""
        return ctx.fetch(url)

    def validate_crawler(source: str) -> dict[str, object]:
        """Run a candidate crawler in the §4.4 sandbox and report what it yielded.

        Returns the positions found (``item_count`` + capped ``samples`` whose
        descriptions are clipped previews — the scorer grades the full extracted
        text), any ``schema_errors`` naming missing or invalid Job fields (url /
        title / company / location / description / source), and any top-level
        ``exception`` — the write → validate → fix signal to use before SUBMIT."""
        # Bind the sandbox validator at call time (not construction) so building the
        # tools for pure discovery never imports the subprocess-sandbox machinery.
        run_in_sandbox = validate_fn or _sandbox_validate()
        result = run_in_sandbox(source, allowed_hosts=hosts, timeout=validate_timeout)
        return {
            "item_count": result.item_count,
            "samples": [_preview_sample(sample) for sample in result.samples],
            "schema_errors": result.schema_errors,
            "exception": None
            if result.exception is None
            else {
                "type": result.exception.type,
                "message": result.exception.message,
            },
        }

    return [fetch, find_feeds, get_sitemap, validate_crawler]
