"""The crawler contract: the normalized :class:`Job` a crawler yields (§4.6).

A generated ``crawlers/<slug>.py`` exposes ``crawl(ctx) -> Iterable[Job]``; the
runner validates each yielded job against this model before canonicalizing,
hashing, and upserting it into the ``positions`` store (§4.7). ``url`` is the
canonical dedup key (see :mod:`jobcrawler.urls`); ``source`` is the site slug.

**Required vs. extract-when-present.** The five content strings plus ``source``
are *required and non-empty* — a posting with no title/company/location/
description is not a job we can track, match, or de-dupe, so it never validates
(the §6 gates layer the stricter length/quality checks on top). Everything else
is *optional*: a crawler fills a field only when the source actually states it,
leaving it ``None`` otherwise. That split is deliberate — ATS boards differ
wildly in what they expose (Greenhouse has no salary/employment fields at all;
Lever always has ``workplaceType``; Ashby has structured compensation ~70% of
the time) — so the optional fields are opportunistic enrichment, never a
contract the generator must satisfy.

**Never invent salary (§4.6).** ``salary`` is the *verbatim* compensation text
as printed; the structured ``salary_min``/``salary_max``/``salary_currency``/
``salary_period`` are set only when the source exposes them as structured data.
A crawler must never parse, guess, or synthesize a number the page did not
state — the host-side deterministic parser (:func:`jobcrawler.runtime.normalize.
parse_salary`) is the single place text→number extraction happens, and it too is
conservative (``None`` when ambiguous). Raw fields (``workplace_type``,
``salary_period``, ``employment_type``) are stored *as stated*; normalization
into the closed taxonomies lives host-side in :mod:`normalize`, never in the
untrusted crawler.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field

from jobcrawler.urls import normalize_host


class Job(BaseModel):
    """One open position a crawler yields, validated before dedup/upsert (§4.6)."""

    # --- required: the identity + content every trackable posting must have ---
    url: str = Field(min_length=1)  # canonical dedup key (jobcrawler.urls)
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)  # site slug

    # --- optional: filled only when the source actually states it ------------
    # Compensation. ``salary`` is the verbatim printed text; the structured
    # quartet is set only from source-structured data (never parsed by the
    # crawler — that is normalize.parse_salary's job, host-side).
    salary: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # 'year' | 'month' | 'hour' as stated

    # Classification inputs, stored raw (host-side normalize.py maps them into
    # the closed taxonomies — see §4.6/§8: no interpretation in the crawler).
    employment_type: str | None = None
    workplace_type: str | None = None  # raw 'remote'/'hybrid'/'onsite' as stated
    department: str | None = None

    # Lifecycle timestamps (drive §4.9 recheck + expiry-based closure).
    posted_at: datetime | None = None
    expires_at: datetime | None = None

    # Stable ATS identity + the canonical apply link.
    external_id: str | None = None  # stable ATS id, when the source exposes one
    apply_url: str | None = None


@runtime_checkable
class Ctx(Protocol):
    """The single fetch boundary a crawler is handed (§4.5).

    All network access goes through ``ctx`` so timeouts, the SSRF guard, and
    encoding are enforced centrally now, and robots / rate-limit / Cloudflare
    join them with the M2 politeness layer — the script declares *what* to
    fetch, not *how*. ``fetch`` is method-general (§8): a board whose
    enumeration endpoint only answers POST (a JSON search API) passes
    ``method="POST"`` + ``json=...``, and the request rides exactly the same
    SSRF / politeness / budget enforcement as a GET — the crawler chooses the
    verb, never the guard.
    """

    def fetch(
        self,
        url: str,
        *,
        max_redirects: int = 5,
        method: str = "GET",
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str: ...
    def render(self, url: str) -> str: ...
    def get(self, url: str, **kwargs: object) -> object: ...
    def log(self, *args: object, **kwargs: object) -> None: ...


CrawlFn = Callable[[Ctx], Iterable[Job]]


def load_crawler(module: object) -> tuple[str, CrawlFn]:
    """Return ``(SITE, crawl)`` from a validated crawler module (§4.3)."""
    site = getattr(module, "SITE", None)
    crawl = getattr(module, "crawl", None)
    if not isinstance(site, str) or not site:
        raise TypeError("crawler module must define a non-empty SITE: str")
    if not callable(crawl):
        raise TypeError("crawler module must define crawl(ctx)")
    return site, cast(CrawlFn, crawl)


# -- declared-host derivation (§8) -------------------------------------------
#
# The committed source *text* is the single place the crawl-time SSRF allowlist
# derives from: AST-read, never executed. ``SITE`` names the board itself;
# ``EXTRA_HOSTS`` is the optional tuple of bare hostnames a crawler additionally
# needs (a board whose JSON API lives on a platform sibling domain). Declaration
# is not authorization — gate-1(b) admits an EXTRA_HOSTS entry only when the
# site's own served bytes vouch for it (see generate/gates.py) — but once a
# crawler is committed, these helpers are how every §4.4 call site (crawl /
# validate / probe) reconstructs exactly the allowlist that was admitted.


def _top_level_value(tree: ast.Module, name: str) -> ast.expr | None:
    """The value expression assigned to module-level ``name`` (Assign or AnnAssign)."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return node.value
    return None


def declared_site(source: str) -> str:
    """The module-level ``SITE`` string constant of ``source``, or ``""`` (§4.3)."""
    value = _top_level_value(ast.parse(source), "SITE")
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return ""


def declared_extra_hosts(source: str) -> tuple[str, ...]:
    """The module-level ``EXTRA_HOSTS`` string literals of ``source`` (§8).

    Only plain string constants inside a literal tuple/list count — the same
    constants-only discipline gate-1 enforces at admission, so this read can
    never be steered by anything the module would *compute* at exec time.
    Anything else (absent, non-literal, non-string entries) reads as empty here;
    the gate is where malformed declarations are rejected loudly.
    """
    value = _top_level_value(ast.parse(source), "EXTRA_HOSTS")
    if not isinstance(value, (ast.Tuple, ast.List)):
        return ()
    return tuple(
        el.value
        for el in value.elts
        if isinstance(el, ast.Constant) and isinstance(el.value, str)
    )


def allowed_hosts_of(source: str) -> set[str]:
    """The fetch allowlist a committed crawler's source declares (§8).

    ``SITE``'s host plus every ``EXTRA_HOSTS`` entry, normalized; empty/broken
    values drop out. This is *policy* — which public hosts the crawler talks
    to; the fixed enforcement kernel (:mod:`jobcrawler.runtime.ssrf` hard
    denials, checked per fetch and per redirect hop) applies to every member
    regardless of how it got here.
    """
    hosts = {normalize_host(declared_site(source))}
    hosts.update(normalize_host(f"https://{h}") for h in declared_extra_hosts(source))
    hosts.discard("")
    return hosts
