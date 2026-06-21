"""The §6 hard acceptance gates — fixed, host-owned binary adjudicators.

These gates are an **immutable trusted computing base** (§8): never authored,
parameterized, or relaxed by model-generated code or the GEPA-optimized
predictor. They return *semantic* failure strings (GEPA feedback says **what**
failed, never the predicate/threshold a candidate could target), and are run
**cheapest-first, short-circuiting** (§9): AST → completeness → stability.

  * Gate 1 ``check_ast`` — an import/builtin allowlist over ``crawler_source.code``
    plus the §4.3 contract shape; a non-parsing source is treated as a truncated
    FINAL channel.
  * Gate 2 ``check_completeness`` — the hard half of runtime validation over a
    ``CandidateResult`` (item floor, per-sample title/body/url, mojibake). The
    *graded* axes for selection live in ``scoring.py``.
  * Gate 2(a) ``must_observe_failure`` — enforces "validate before SUBMIT" and
    "consult feeds/sitemap before choosing scrape" (the anti-under-enumeration
    discipline [Sanjaya]).
  * Gate 3 ``stability_verdict`` — two-run Jaccard over **canonical** URLs;
    hard-reject only the near-random case, near-threshold is soft feedback. Kept
    out of the optimizer scalar so a flaky reject can't poison selection.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from bizup_autocrawler.generate.scoring import CandidateResult
from bizup_autocrawler.urls import canonicalize_url, valid_on_domain_url


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.failures)


# -- gate 1: AST allowlist ---------------------------------------------------

# Exact module names a generated crawler may import. urllib is intentionally
# narrowed to ``urllib.parse`` (``urllib.request`` would be a network egress
# path outside ctx). __future__ is the harmless annotations directive.
_ALLOWED_EXACT = frozenset(
    {
        "__future__",
        "re",
        "json",
        "datetime",
        "urllib.parse",
        "collections",
        "collections.abc",
        "itertools",
        "math",
        "html",
        "string",
        "typing",
        "dataclasses",
        "bizup_autocrawler.runtime.contract",
    }
)
# Parser packages: the package and any submodule (lxml.etree, selectolax.parser).
# stdlib ``xml`` (xml.etree/xml.dom/xml.sax) is permitted because the §8
# hardening (runtime/hardening.py, applied before any crawler runs) defuses XXE /
# entity-expansion in every execution path — so it is a safe feed/sitemap parser.
_ALLOWED_PREFIXES = ("selectolax", "lxml", "feedparser", "xml")
# Builtins that grant filesystem/eval/introspection escape.
_BANNED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
)


def _module_allowed(name: str) -> bool:
    if name in _ALLOWED_EXACT:
        return True
    return any(name == p or name.startswith(p + ".") for p in _ALLOWED_PREFIXES)


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def check_ast(source: str) -> GateResult:
    """Gate 1: import/builtin allowlist + §4.3 contract shape (§8)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return GateResult(
            False, (f"source does not parse (truncated FINAL channel?): {exc.msg}",)
        )

    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    failures.append(f"banned import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                failures.append("relative import not allowed")
            elif not _module_allowed(node.module or ""):
                failures.append(f"banned import: {node.module}")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            failures.append(f"banned builtin: {node.id}")
        elif isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            failures.append(f"banned dunder access: {node.attr}")

    failures.extend(_check_contract(tree))
    return GateResult(not failures, tuple(failures))


def _check_contract(tree: ast.Module) -> list[str]:
    """The §4.3 shape: a module-level SITE and a crawl(ctx) at top level."""
    has_site = False
    crawl: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SITE" for t in node.targets
        ):
            has_site = True
        elif isinstance(node, ast.AnnAssign) and (
            isinstance(node.target, ast.Name) and node.target.id == "SITE"
        ):
            has_site = True
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "crawl"
        ):
            crawl = node

    failures: list[str] = []
    if not has_site:
        failures.append("module must define a top-level SITE constant")
    if crawl is None:
        failures.append("module must define crawl(ctx)")
    elif len(crawl.args.posonlyargs) + len(crawl.args.args) < 1:
        failures.append("crawl must accept a ctx parameter")
    return failures


# -- gate 2: runtime completeness (hard half) --------------------------------


def check_completeness(
    result: CandidateResult,
    *,
    min_items: int,
    min_body_chars: int,
    allowed_hosts: Collection[str],
    expected_items: int | None = None,
    enum_floor_ratio: float = 0.5,
    min_body_quality: float = 0.6,
    max_replacement_ratio: float = 0.02,
) -> GateResult:
    """Gate 2: item floor + enumeration floor + per-sample validity (§6).

    ``expected_items`` is the host-estimated count of recent articles the site
    actually publishes (e.g. from its news sitemap). When known, the crawler must
    enumerate at least ``enum_floor_ratio`` of it — the anti-under-enumeration
    discipline [Sanjaya]: clearing the small absolute ``min_items`` floor is not
    enough if the site has far more. Dormant when ``expected_items`` is None.
    """
    failures: list[str] = []
    if result.exception is not None:
        failures.append(
            f"crawler raised {result.exception.type}: {result.exception.message}"
        )
    if result.item_count < min_items:
        failures.append(f"yielded {result.item_count} items, need >= {min_items}")
    elif expected_items is not None and expected_items > 0:
        floor = math.ceil(enum_floor_ratio * expected_items)
        if result.item_count < floor:
            failures.append(
                f"enumerated {result.item_count} of ~{expected_items} recent articles"
            )
    if result.schema_errors:
        failures.append(
            f"{len(result.schema_errors)} schema errors (e.g. {result.schema_errors[0]})"
        )
    shared = _shared_body_block(result.samples)
    if shared is not None:
        failures.append(
            f"samples share a repeated body block (boilerplate): {shared[:60]!r}"
        )

    short = 0
    for i, sample in enumerate(result.samples):
        title = str(sample.get("title") or "").strip()
        body = str(sample.get("body") or "").strip()
        url = str(sample.get("url") or "")
        if not title:
            failures.append(f"sample {i}: empty title")
        if len(body) < min_body_chars:
            short += 1  # body length is graded by fraction below, not per-sample
        elif _replacement_ratio(body) > max_replacement_ratio:
            failures.append(f"sample {i}: high U+FFFD ratio (mojibake)")
        if not valid_on_domain_url(url, allowed_hosts):
            failures.append(f"sample {i}: invalid/off-domain url {url!r}")

    # Body completeness is a fraction, not all-or-nothing: a minority of short posts
    # (a news brief) is fine — the runtime filter drops them (§4.6), so generation
    # isn't stricter than runtime. A mostly-summary crawler still fails.
    n = len(result.samples)
    if n and (n - short) / n < min_body_quality:
        failures.append(f"{short} of {n} sample bodies too short (mostly incomplete)")

    return GateResult(not failures, tuple(failures))


def _replacement_ratio(text: str) -> float:
    return text.count("�") / max(len(text), 1)


def _shared_body_block(samples: list[dict], *, min_block_chars: int = 80) -> str | None:
    """A substantial body block shared by >= 2 samples — a related-links / most-read
    rail scraped as article body (§6 low-boilerplate). Returns the first such block.

    Bodies are split on blank lines; only blocks of ``min_block_chars`` or more are
    considered, so legitimately-shared short strings (datelines, "read more") don't
    trip it. Real article paragraphs are effectively unique across articles.
    """
    counts: dict[str, int] = {}
    for sample in samples:
        body = str(sample.get("body") or "")
        blocks = {
            stripped
            for block in body.split("\n\n")
            if len(stripped := block.strip()) >= min_block_chars
        }
        for block in blocks:
            counts[block] = counts.get(block, 0) + 1
    for block, count in counts.items():
        if count >= 2:
            return block
    return None


def must_observe_failure(
    *, validated: bool, discovery_method: str, consulted_discovery: bool
) -> str | None:
    """Gate 2(a): enforce validate-first and consult-feeds-before-scrape (§6)."""
    if not validated:
        return "candidate never called validate_crawler before SUBMIT"
    if discovery_method == "scrape" and not consulted_discovery:
        return "chose discovery_method=scrape without consulting find_feeds/get_sitemap"
    return None


# -- gate 3: two-run Jaccard stability ---------------------------------------


class StabilityVerdict(Enum):
    PASS = "pass"
    SOFT = "soft"  # near-threshold: surface as soft feedback for the review path
    REJECT = "reject"  # near-random: the only hard reject


def jaccard_stability(run1: Collection[str], run2: Collection[str]) -> float:
    """Jaccard overlap of the two runs' **canonical** URL sets (§4.6)."""
    a = {canonicalize_url(u) for u in run1}
    b = {canonicalize_url(u) for u in run2}
    union = a | b
    if not union:
        return 1.0  # both empty (the item-floor gate handles emptiness first)
    return len(a & b) / len(union)


def stability_verdict(
    run1: Collection[str],
    run2: Collection[str],
    *,
    threshold: float,
    reject_floor: float = 0.1,
) -> tuple[StabilityVerdict, float]:
    """Pass at/above threshold, hard-reject at/below the near-random floor, else soft."""
    j = jaccard_stability(run1, run2)
    if j >= threshold:
        return StabilityVerdict.PASS, j
    if j <= reject_floor:
        return StabilityVerdict.REJECT, j
    return StabilityVerdict.SOFT, j
