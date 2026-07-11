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
boilerplate masquerading as a description, and descriptions that collapse toward
one shared blurb — a strict majority byte-identical across different titles — are
a crawler grabbing one page-level blob for every role (a *minority* of cross-title
clones is tolerated: real boards post one req per location with the city in the
title). Shared boilerplate
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


# ``str.format`` / ``str.format_map`` parse a replacement-field mini-language at
# *runtime* — ``"{0.__class__.__init__.__globals__[...]}".format(obj)`` walks
# attributes and indexes on the arguments from inside a plain string literal, so
# none of that traversal is an ``ast.Attribute`` this gate can see. That is the
# canonical way to reach a dunder past the static dunder-attribute ban, and with
# the capability facade (§8) it is how hostile code would try to prise the real
# ctx back out of a method closure. Ban the two method *names* outright (any
# ``.format``/``.format_map`` access, so ``f = s.format; f(...)`` is caught too);
# f-strings do the same job and *are* AST-visible, so the existing dunder checks
# already cover them. The sibling ``string.Formatter`` route is closed by dropping
# ``string`` from the import allowlist (``runtime.safe_exec``).
_FORMAT_METHODS = frozenset({"format", "format_map"})


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
        elif isinstance(node, ast.Attribute) and node.attr in _FORMAT_METHODS:
            failures.append(
                f"banned str method: .{node.attr} — its replacement fields resolve "
                "attribute/index access at runtime, past this gate (the "
                "'{0.__class__}' escape); build strings with f-strings instead"
            )
        elif isinstance(node, ast.Constant) and node.value == "salary_interval":
            # Not a Job field — pydantic would drop it SILENTLY (extra='ignore'),
            # which is how 13 generated crawlers shipped period-less salaries.
            failures.append(
                "misnamed contract field: 'salary_interval' — the Job field is "
                "salary_period (pass the source's interval wording as stated; "
                "the host maps '1 YEAR'/'1 MONTH'/'1 HOUR')"
            )

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

# Well-known public ATS / job-board backends (§8). These are fixed, multi-tenant
# vendor domains that serve nothing but public job listings: a company careers page
# hosted on one of them (or backed by its API on a sibling host) legitimately fetches
# that vendor host even though the registry URL never named it — e.g. a rippling.com
# careers page whose postings live at boards-api.greenhouse.io. They carry no SSRF
# value (public, read-only, no path to internal infrastructure), so an on-domain match
# here authorizes a declared EXTRA_HOST *without* a per-site operator declaration —
# sparing the operator from hand-authorizing Greenhouse for each of the 130+ boards
# already running on it. Two properties keep this safe: the list is code-level, so an
# attacker-influenced page can never extend it (it can only name a host already on it,
# which is a benign public endpoint); and every entry still clears the fixed SSRF
# kernel below. Matched with the suffix-safe host_on_domain primitive, so a look-alike
# like "greenhouse.io.evil.example" is NOT admitted. Entries are registrable domains,
# which subsume regional/tenant subdomains (boards-api.eu.greenhouse.io, api.lever.co).
ATS_BACKEND_DOMAINS: frozenset[str] = frozenset(
    {
        "greenhouse.io",
        "ashbyhq.com",
        "lever.co",
        "workable.com",
        "smartrecruiters.com",
        "recruitee.com",
        "bamboohr.com",
        "personio.com",
        "pinpointhq.com",
        "gem.com",
        "comeet.co",
        "icims.com",
        "gupy.io",
        "myworkdayjobs.com",
        "csod.com",
    }
)


def is_ats_backend(host: str) -> bool:
    """True if ``host`` is (a subdomain of) a well-known public ATS backend (§8).

    The autonomy relaxation for :func:`check_host_admission`: such a host is admissible
    as a declared EXTRA_HOST even without operator ``extra_hosts`` evidence, because it
    is a fixed public job-board vendor endpoint (:data:`ATS_BACKEND_DOMAINS`) with no
    SSRF value. The SSRF kernel still vets it; only the operator-authorization policy
    is waived. Suffix-safe via ``host_on_domain``.
    """
    return host_on_domain(host, ATS_BACKEND_DOMAINS)


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
      * Every ``EXTRA_HOSTS`` entry must be **operator-authorized**: on-domain of
        a host in ``evidence_hosts``, *or* a well-known public ATS backend
        (:func:`is_ats_backend`) which needs no per-site evidence. The generation
        context is attacker-influenced (a job page can carry injected instructions and
        name any host it likes), so neither the crawler declaring a host nor that host
        merely appearing in the fetched page content authorizes egress — only the
        operator's out-of-band registry declaration, or membership in the fixed
        code-level ATS allowlist a page cannot extend, does.
      * The set stays small (``cap``) and every entry must clear the fixed SSRF
        kernel — the hard denials apply to every host regardless of provenance.

    ``evidence_hosts`` is the authorization source, and the *caller's* contract is
    to supply the operator-declared registry ``Site.extra_hosts`` for the site —
    NEVER hosts scraped from the fetched corpus. That distinction is the whole §8
    fix: page content is untrusted input, so it can name hosts but can never
    authorize the crawler to fetch them.
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
        if not host_on_domain(host, evidence) and not is_ats_backend(host):
            failures.append(
                f"EXTRA_HOSTS entry {entry!r} is not operator-authorized — add it "
                "to the registry entry's extra_hosts if it is a legitimate backend"
            )
    return GateResult(not failures, tuple(failures))


def admissible_hosts(
    source: str,
    *,
    registry_hosts: Collection[str],
    evidence_hosts: Collection[str],
) -> set[str]:
    """The allowlist admission will grant ``source`` — registry hosts + the declared
    ``EXTRA_HOSTS`` entries the operator authorized (§8).

    Registry host(s) plus every declared ``EXTRA_HOSTS`` entry that clears the same
    rule :func:`check_host_admission` applies (SSRF kernel + a ``host_on_domain``
    match against ``evidence_hosts``). Like that gate, the caller must source
    ``evidence_hosts`` from the operator's registry ``extra_hosts`` — never the
    fetched page content. Generation-time validation and the reward grade a
    candidate under *this* set — the allowlist the committed crawler will actually
    run with — so an undeclared cross-host fetch fails inside the write → validate →
    fix loop with an ``SsrfError`` naming the host, not first at admission after the
    fan-out already picked a winner. A source whose declarations don't parse keeps
    the registry hosts alone (it will lose at gate 1 anyway; validation just runs
    narrower).
    """
    base = {h for h in registry_hosts if h}
    try:
        declared = declared_extra_hosts(source)
    except SyntaxError:
        return base
    evidence = {
        h for h in (normalize_host(f"https://{e}") for e in evidence_hosts) if h
    }
    admitted: set[str] = set()
    for entry in declared:
        host = normalize_host(f"https://{entry}")
        if not host or not (host_on_domain(host, evidence) or is_ats_backend(host)):
            continue
        try:
            check_url(f"https://{host}/", {host})
        except SsrfError:
            continue
        admitted.add(host)
    return base | admitted


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
    max_schema_error_ratio: float = 0.02,
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
    sample fails only when its unique residual falls below ``min_unique_chars``,
    with the whole set failing only when its descriptions collapse toward one
    shared blurb (a strict majority byte-identical across different titles) — so
    the About-us / benefits / EEO slabs a real careers board repeats, and the
    per-location role clones it posts, never sink a correct crawler.

    Schema errors are tolerated up to ``max_schema_error_ratio`` of the total
    yield, mirroring the production runner: ``runner._collect`` drops an invalid
    item, counts it in ``errors=N``, and the crawl proceeds — so a candidate the
    runtime would run fine (say one remote posting with an empty location on a
    hundred-role board) must not die at admission for a sub-ratio wart. The
    errors stay in the ``validate_crawler`` output either way, so the
    write → validate → fix loop still steers toward zero.
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
        total = result.item_count + len(result.schema_errors)
        if len(result.schema_errors) > max_schema_error_ratio * total:
            failures.append(
                f"{len(result.schema_errors)} schema errors across {total} yields "
                f"(e.g. {result.schema_errors[0]})"
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

    # The per-sample url check above only sees the first samples of the yield.
    # ``offdomain_hosts`` is the child's FULL-yield signal — every distinct host a
    # yielded url pointed at that was off the crawler's fetch allowlist — so a
    # crawler that is clean for the sampled few then emits off-domain urls in the
    # tail is still rejected. Re-filter against ``allowed_hosts`` here (the same
    # url_evidence-widened set the per-sample check uses), so a legitimate sibling
    # link-host the child flagged against the narrow fetch allowlist is forgiven
    # while a genuinely unauthorized host fails the whole candidate (§6/§8).
    tail_offdomain = sorted(
        h for h in result.offdomain_hosts if not host_on_domain(h, allowed_hosts)
    )
    if tail_offdomain:
        failures.append(
            f"off-domain url hosts across the full yield: {', '.join(tail_offdomain)}"
        )

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
    """The substantial blocks that appear in >= 2 *distinct* descriptions.

    On one company's board these are the legitimately-repeated slabs (benefits,
    EEO, About-us). We collect them so :func:`_strip_blocks` can remove them before
    measuring each posting's unique residual — the inverted §6 boilerplate check:
    sharing them is expected, hiding a thin description behind them is not.
    Counted over distinct description strings, not raw samples, so a same-req
    region clone (the same posting sampled twice) never promotes its own body to
    "boilerplate" and residual-strips itself to nothing — shared means "appears
    across different postings".
    """
    counts: dict[str, int] = {}
    descriptions = {str(sample.get("description") or "") for sample in samples}
    for description in descriptions:
        # ``set`` so a block repeated within one posting counts that posting once.
        for block in set(_blocks(description)):
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


def _identity_scan(
    samples: list[dict],
) -> tuple[list[tuple[int, int]], list[int], list[int], int]:
    """The §6 identity facts: cross-title identical pairs, the collapsed set, the
    counted indices, and the count of DISTINCT description bodies.

    Returns ``(pairs, collapsed, counted, distinct)``: each pair is two sample
    indices whose descriptions are byte-identical under *different* titles;
    ``collapsed`` is every counted index when the whole set reduces to a single
    (title, description) posting (else empty); ``counted`` is the indices with a
    non-empty description; and ``distinct`` is how many distinct description
    strings those span. Samples with empty descriptions are ignored (the
    length/quality checks own that case). One scan feeds both the gate's failure
    messages and the description axis's defect set, so the identity rules cannot
    drift between enforcement and reward.
    """
    seen: dict[str, tuple[int, str]] = {}
    pairs: list[tuple[int, int]] = []
    postings: set[tuple[str, str]] = set()
    counted: list[int] = []
    for i, sample in enumerate(samples):
        description = str(sample.get("description") or "")
        if not description.strip():
            continue
        title = str(sample.get("title") or "").strip()
        counted.append(i)
        postings.add((title, description))
        if description in seen:
            first, first_title = seen[description]
            if title != first_title:
                pairs.append((first, i))
        else:
            seen[description] = (i, title)
    collapsed = counted if len(counted) >= 2 and len(postings) == 1 else []
    return pairs, collapsed, counted, len(seen)


def _descriptions_collapse(counted: list[int], distinct: int) -> bool:
    """True when the sampled descriptions collapse toward one shared blurb (§6).

    The blurb-grab bug stamps one board-level blob on *each* row, so its
    signature is a **strict majority** of postings reusing another's body: fewer
    distinct bodies than half the sampled postings, or (at any size) a single
    distinct body across them all. A **minority** of cross-title identical bodies
    is NOT this bug — real boards post one req per location with the city in the
    title (``Account Manager - DC`` / ``- New York``), so pagerduty samples 15
    roles with 14 distinct bodies and one clone pair. Rejecting that minority
    makes correct boards ungeneratable, so we fire only past the halfway mark.
    Gate and §4.11 reward axis both consult this predicate, so tolerance and
    penalty stay aligned.
    """
    n = len(counted)
    return n >= 2 and (distinct == 1 or 2 * distinct < n)


def _identical_description_failures(samples: list[dict]) -> list[str]:
    """Gate message when the sampled descriptions collapse toward one blurb (§6).

    A crawler that yields the *same* description for postings under *many*
    different titles grabbed one page-level generic blurb instead of the per-role
    text — broken even though each row clears the length floor. But a real ATS
    board legitimately posts one req per location as separate postings with
    identical bodies (``Account Manager - DC`` / ``- New York``, or GitLab's
    per-region clones), so a MINORITY of cross-title clones is tolerated: only a
    strict-majority collapse (:func:`_descriptions_collapse`) fails. A sample set
    that collapses to a single (title, description) posting always fails — that is
    one job echoed n times, not a crawl.
    """
    pairs, collapsed, counted, distinct = _identity_scan(samples)
    if collapsed:
        return [f"all {len(collapsed)} samples are the same posting"]
    if pairs and _descriptions_collapse(counted, distinct):
        return [
            f"descriptions collapse to {distinct} distinct across "
            f"{len(counted)} sampled postings (one blurb stamped on most roles)"
        ]
    return []


def defective_description_indices(
    samples: list[dict], *, min_unique_chars: int
) -> set[int]:
    """Sample indices whose descriptions flunk the §6 uniqueness rules.

    The union of: every posting sharing a body in a strict-majority collapse
    (:func:`_descriptions_collapse` — a MINORITY of cross-title clones is
    tolerated, matching the gate), every sample when the set collapses to a
    single (title, description) posting, and samples whose unique residual after
    stripping the board's shared blocks falls below ``min_unique_chars``. This is
    the "§6 unique-residual measure" the §4.11 description axis grades by (§8
    adversarial admission): the reward steers by exactly the defects this gate
    enforces — a candidate that stamps one page-level blurb on every posting
    scores low *before* it ever reaches the gate, instead of sailing to the gate
    on length alone. Thresholds stay hidden from the generator either way (§8).
    """
    pairs, collapsed, counted, distinct = _identity_scan(samples)
    defective: set[int] = set(collapsed)
    if _descriptions_collapse(counted, distinct):
        defective.update(i for pair in pairs for i in pair)
    shared = _shared_blocks(samples)
    for i, sample in enumerate(samples):
        description = str(sample.get("description") or "").strip()
        if description and len(_strip_blocks(description, shared)) < min_unique_chars:
            defective.add(i)
    return defective


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
