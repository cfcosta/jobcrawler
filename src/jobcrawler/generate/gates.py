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
    ``CandidateResult`` (position floor + §6 enum floor, per-sample
    title/company/location/description/url, mojibake, boilerplate residual). The
    *graded* axes for selection live in ``scoring.py``.
  * Gate 2(a) ``must_observe_failure`` — enforces "validate before SUBMIT" and
    "consult feeds/sitemap before choosing scrape" (the anti-under-enumeration
    discipline [Sanjaya]).
  * Gate 3 ``stability_verdict`` — two-run Jaccard over **canonical** URLs;
    hard-reject only the near-random case, near-threshold is soft feedback. Kept
    out of the optimizer scalar so a flaky reject can't poison selection.

**The boilerplate gate is INVERTED for job boards (§6).** In the news port this
gate *rejected* any body block shared across samples — a related-links / most-read
rail scraped as article text. That predicate is exactly backwards for a jobs
board: every posting on one company's ATS legitimately repeats large blocks (the
About-us blurb, the benefits list, the EEO statement), so a mechanical news→jobs
port of this gate would reject every *correct* crawler. Instead we STRIP the
shared blocks and judge what remains: a posting whose unique residual — after
removing every block (≥ 80 chars) shared by ≥ 2 samples — is too thin is
boilerplate masquerading as a description, and two byte-identical descriptions
are a crawler grabbing one page-level blurb for every role. Shared boilerplate
*plus* a substantial unique residual PASSES: that is the normal, healthy shape of
a careers board, and this is exactly the spot a naive port would get wrong.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from jobcrawler.generate.scoring import CandidateResult
from jobcrawler.runtime.contract import declared_extra_hosts, declared_site
from jobcrawler.runtime.safe_exec import BANNED_BUILTINS, module_allowed
from jobcrawler.runtime.ssrf import SsrfError, check_url
from jobcrawler.urls import (
    canonicalize_url,
    host_on_domain,
    normalize_host,
    valid_on_domain_url,
)


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        return "; ".join(self.failures)


# -- gate 1: AST allowlist ---------------------------------------------------

# The import allowlist (``module_allowed``) and the banned-builtin set
# (``BANNED_BUILTINS``) are owned by ``runtime.safe_exec`` — the single source of
# truth shared with the *runtime* half of the sandbox (the restricted
# ``__builtins__`` both subprocess entries exec under). One definition means the
# static gate and the runtime sandbox can never drift apart and leave a hole one
# side has closed.


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
                if not module_allowed(alias.name):
                    failures.append(f"banned import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                failures.append("relative import not allowed")
            elif not module_allowed(node.module or ""):
                failures.append(f"banned import: {node.module}")
        elif isinstance(node, ast.Name) and node.id in BANNED_BUILTINS:
            failures.append(f"banned builtin: {node.id}")
        elif isinstance(node, ast.Name) and _is_dunder(node.id):
            # ``__builtins__`` (and any dunder name) is the canonical gate-bypass
            # handle: reach the builtins mapping by name, then index a raw
            # ``__import__``/``eval`` out of it. Ban every dunder name, symmetric
            # with the dunder-attribute escape ban below.
            failures.append(f"banned dunder name: {node.id}")
        elif isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            failures.append(f"banned dunder access: {node.attr}")

    failures.extend(_check_contract(tree))
    return GateResult(not failures, tuple(failures))


def _check_contract(tree: ast.Module) -> list[str]:
    """The §4.3 shape: a module-level SITE and a crawl(ctx) at top level."""
    has_site = False
    crawl: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    extra_hosts: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "SITE" in names:
                has_site = True
            if "EXTRA_HOSTS" in names:
                extra_hosts = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "SITE":
                has_site = True
            elif node.target.id == "EXTRA_HOSTS" and node.value is not None:
                extra_hosts = node.value
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
    if extra_hosts is not None:
        failures.extend(_check_extra_hosts_shape(extra_hosts))
    return failures


# Bare hostname: dot-separated LDH labels, no scheme/path/port. The shape gate for
# EXTRA_HOSTS entries — anything else could smuggle a path or scheme into the
# allowlist derivation (§8).
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


def _check_extra_hosts_shape(value: ast.expr) -> list[str]:
    """EXTRA_HOSTS, when declared, is a literal tuple/list of bare-hostname strings.

    Constants-only, statically (§8): the crawl-time allowlist derivation
    (``contract.allowed_hosts_of``) AST-reads exactly these literals, so anything
    computed — an f-string, a name, a call — must be rejected here or the gate
    would admit an allowlist it cannot read.
    """
    if not isinstance(value, (ast.Tuple, ast.List)):
        return ["EXTRA_HOSTS must be a literal tuple/list of hostname strings"]
    failures: list[str] = []
    for el in value.elts:
        if not (isinstance(el, ast.Constant) and isinstance(el.value, str)):
            failures.append("EXTRA_HOSTS entries must be plain string literals")
        elif not _HOSTNAME_RE.match(el.value):
            failures.append(
                f"EXTRA_HOSTS entry {el.value!r} is not a bare hostname "
                "(no scheme, path, or port)"
            )
    return failures


# -- gate 1(b): host admission (§8) -------------------------------------------


def check_host_admission(
    source: str,
    *,
    registry_hosts: Collection[str],
    evidence_hosts: Collection[str],
    cap: int,
) -> GateResult:
    """Gate 1(b): the crawler's declared host envelope must be admissible (§8).

    The autonomy boundary for the allowlist — *policy is inferred, enforcement is
    fixed*. A generated crawler chooses which public hosts it talks to, but only
    within what trusted host code has vouched for:

      * ``SITE``'s host must be the registry entry's — the registry URL is what
        fixes a site's allowlist (§4.1), so a winner that drifted to a different
        board is rejected outright.
      * Every ``EXTRA_HOSTS`` entry must be **evidence-backed**: a host the
        site's own served bytes name (or its board platform documents — see
        ``tools.observed_hosts``). The generation context is attacker-influenced
        (a job page can carry injected instructions), so declaration alone
        authorizes nothing.
      * The set stays small (``cap``) and every entry must clear the fixed SSRF
        kernel — the hard denials apply to every host regardless of provenance.
    """
    failures: list[str] = []
    site_host = normalize_host(declared_site(source))
    registry = {h for h in registry_hosts if h}
    if not site_host or site_host not in registry:
        failures.append(
            f"SITE host {site_host or '(unparseable)'} is not the registry site"
        )

    declared = declared_extra_hosts(source)
    if len(declared) > cap:
        failures.append(f"declares {len(declared)} EXTRA_HOSTS, cap is {cap}")
    evidence = {
        h for h in (normalize_host(f"https://{e}") for e in evidence_hosts) if h
    }
    for entry in declared:
        host = normalize_host(f"https://{entry}")
        if not host:
            failures.append(f"EXTRA_HOSTS entry {entry!r} is not a valid hostname")
            continue
        try:
            check_url(f"https://{host}/", {host})
        except SsrfError as exc:
            failures.append(
                f"EXTRA_HOSTS entry {entry!r} is denied by the SSRF kernel: {exc}"
            )
            continue
        if not host_on_domain(host, evidence):
            failures.append(
                f"EXTRA_HOSTS entry {entry!r} is not observed in the site's "
                "own served content"
            )
    return GateResult(not failures, tuple(failures))


# -- gate 2: runtime completeness (hard half) --------------------------------


def check_completeness(
    result: CandidateResult,
    *,
    min_items: int,
    min_description_chars: int,
    allowed_hosts: Collection[str],
    expected_items: int | None = None,
    enum_floor_ratio: float = 0.5,
    min_description_quality: float = 0.6,
    min_unique_chars: int = 150,
    max_replacement_ratio: float = 0.02,
) -> GateResult:
    """Gate 2: position floor + enumeration floor + per-sample validity (§6).

    ``expected_items`` is the host-estimated count of openings the board
    currently lists (e.g. an ATS ``meta.total`` — Greenhouse/Ashby hand back the
    whole board in one call, so the count is exact). When known, the crawler must
    enumerate at least ``enum_floor_ratio`` of it — the anti-under-enumeration
    discipline [Sanjaya]: clearing the small absolute ``min_items`` floor is not
    enough if the board advertises far more roles. Dormant when ``expected_items``
    is None (a source that only shows a recent window — ``enumerates_all: false``
    — never sets it, since a diff against a partial listing is unsound).

    The boilerplate check is INVERTED from the news port (see the module
    docstring): blocks shared across samples are STRIPPED, not rejected, and a
    sample fails only when its unique residual falls below ``min_unique_chars`` or
    two samples' descriptions are byte-identical — so the About-us / benefits /
    EEO slabs a real careers board repeats never sink a correct crawler.
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
                f"enumerated {result.item_count} of ~{expected_items} openings the board lists"
            )
    if result.schema_errors:
        failures.append(
            f"{len(result.schema_errors)} schema errors (e.g. {result.schema_errors[0]})"
        )
    failures.extend(_identical_description_failures(result.samples))

    # Blocks (>= 80 chars, blank-line split) shared by >= 2 samples are the
    # LEGITIMATE boilerplate of one company's board (About-us, benefits, EEO):
    # strip them, then judge each posting on its unique residual — never reject a
    # crawler merely for sharing them (the §6 inversion; see the module docstring).
    shared = _shared_blocks(result.samples)

    short = 0
    for i, sample in enumerate(result.samples):
        title = str(sample.get("title") or "").strip()
        company = str(sample.get("company") or "").strip()
        location = str(sample.get("location") or "").strip()
        description = str(sample.get("description") or "").strip()
        url = str(sample.get("url") or "")
        if not title:
            failures.append(f"sample {i}: empty title")
        if not company:
            failures.append(f"sample {i}: empty company")
        if not location:
            failures.append(f"sample {i}: empty location")
        if len(description) < min_description_chars:
            short += 1  # description length is graded by the fraction below
        else:
            # Only a full-length description is checked for garble and boilerplate:
            # a terse one is already counted toward the completeness fraction.
            if _replacement_ratio(description) > max_replacement_ratio:
                failures.append(f"sample {i}: high U+FFFD ratio (mojibake)")
            residual = len(_strip_blocks(description, shared))
            if residual < min_unique_chars:
                failures.append(
                    f"sample {i}: description is mostly shared boilerplate "
                    f"(unique residual {residual} chars)"
                )
        if not valid_on_domain_url(url, allowed_hosts):
            failures.append(f"sample {i}: invalid/off-domain url {url!r}")

    # Description completeness is a fraction, not all-or-nothing: a minority of
    # genuinely-terse postings (a one-line contract role) is fine — the runtime
    # filter drops them (§4.6), so generation isn't stricter than runtime. A
    # crawler that scrapes only title + teaser instead of the full posting fails.
    n = len(result.samples)
    if n and (n - short) / n < min_description_quality:
        failures.append(
            f"{short} of {n} sample descriptions too short (mostly incomplete)"
        )

    return GateResult(not failures, tuple(failures))


def _replacement_ratio(text: str) -> float:
    return text.count("�") / max(len(text), 1)


def _blocks(text: str, *, min_block_chars: int = 80) -> list[str]:
    """The *substantial* paragraphs of ``text``: blank-line split, stripped, and at
    least ``min_block_chars`` long.

    Short shared strings (a dateline, a lone location line, "Apply now") fall below
    the floor so they never count as a boilerplate block — only the large repeated
    About-us / benefits / EEO slabs a single board serves on every posting do.
    """
    return [
        stripped
        for block in text.split("\n\n")
        if len(stripped := block.strip()) >= min_block_chars
    ]


def _shared_blocks(samples: list[dict]) -> frozenset[str]:
    """The substantial blocks that appear in >= 2 samples' descriptions.

    On one company's board these are the legitimately-repeated slabs (benefits,
    EEO, About-us). We collect them so :func:`_strip_blocks` can remove them before
    measuring each posting's unique residual — the inverted §6 boilerplate check:
    sharing them is expected, hiding a thin description behind them is not.
    """
    counts: dict[str, int] = {}
    for sample in samples:
        # ``set`` so a block repeated within one posting counts that posting once.
        for block in set(_blocks(str(sample.get("description") or ""))):
            counts[block] = counts.get(block, 0) + 1
    return frozenset(block for block, count in counts.items() if count >= 2)


def _strip_blocks(description: str, shared: Collection[str]) -> str:
    """``description`` minus every shared boilerplate block — the unique residual.

    Splits on the same blank-line boundary as :func:`_blocks` but keeps *every*
    non-shared segment (including sub-80-char ones — a short but distinct role
    summary is still unique text); only the blocks in ``shared`` are dropped. So a
    posting that is nothing but the shared slabs residual-strips to ~nothing, while
    one with real per-role prose keeps it.
    """
    if not shared:
        return description.strip()
    kept = [
        stripped
        for segment in description.split("\n\n")
        if (stripped := segment.strip()) and stripped not in shared
    ]
    return " ".join(kept)


def _identical_description_failures(samples: list[dict]) -> list[str]:
    """Pairs of samples whose full descriptions are byte-identical (§6).

    A crawler that yields the *same* description for every posting — grabbing one
    page-level generic blurb instead of the per-role text — is broken even though
    each row clears the length floor. Empty descriptions are ignored here (the
    length/quality checks own that case), so this fires only on real duplication.
    """
    seen: dict[str, int] = {}
    failures: list[str] = []
    for i, sample in enumerate(samples):
        description = str(sample.get("description") or "")
        if not description.strip():
            continue
        if description in seen:
            failures.append(
                f"samples {seen[description]} and {i} have identical descriptions"
            )
        else:
            seen[description] = i
    return failures


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
