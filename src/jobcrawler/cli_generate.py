"""The ``jobcrawl generate`` orchestration (§4.2, §6) — needs a live LM + Deno.

Kept separate from ``cli.py`` so importing the CLI never pulls dspy. The full §6
machinery runs here:

  1. prefetch the corpus through ctx;
  2. build the §4.2 host tools (fetch / find_feeds / get_sitemap / validate_crawler)
     and an explicit ``sub_lm`` (never the silent root fallback) and hand them to
     the RLM;
  3. generate N candidates (distinct rollout_id @ temp 1.0) and argmax over the
     **graded scalar** via ``best_of_n`` + ``make_reward_fn`` (the core runs the
     candidate through the §4.4 sandbox);
  4. gate the winner with the full set — AST → must-observe (validate-before-SUBMIT
     / consult-feeds-before-scrape, read off the trajectory) → runtime completeness
     → two-run Jaccard stability — and, only on a pass, *deliver* the crawler: an
     operator-run ``generate`` writes ``crawlers/<slug>.py`` directly, but the
     unattended §4.8 self-heal (``regenerate_one``) honors the tri-state
     ``JOBCRAWLER_REVIEW_GATE``: ``on`` (default) *stages* it to
     ``crawlers/_pending/<slug>.py`` for a human to ``promote`` (§14.2); ``auto``
     backs the live crawler up to ``crawlers/_history/`` and promotes it itself
     (reversible via ``rollback``); ``off`` writes live with no backup.

The whole path is live (LM, Deno, network); the gating + delivery logic is unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Collection
from pathlib import Path

import dspy
import httpx

from jobcrawler import config
from jobcrawler.generate.gates import (
    StabilityVerdict,
    admissible_hosts,
    check_ast,
    check_completeness,
    check_host_admission,
    must_observe_failure,
    stability_verdict,
)
from jobcrawler.generate.adversarial import cross_validate
from jobcrawler.generate.budget import CappedLM
from jobcrawler.lm import build_lm
from jobcrawler.generate.generator import best_of_n, load_generator
from jobcrawler.generate.scoring import CandidateResult, make_reward_fn
from jobcrawler.generate.snapshot import prefetch_corpus
from jobcrawler.generate.tools import corpus_host_evidence, make_host_tools
from jobcrawler.health import RegenOutcome
from jobcrawler.runtime.contract import declared_extra_hosts
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.politeness import RobotsPolicy
from jobcrawler.runtime.sandbox import validate_crawler
from jobcrawler.runtime.ssrf import check_url
from jobcrawler.schedule import Site
from jobcrawler.sites import find_site, load_sites
from jobcrawler.trainset import append_record, make_example
from jobcrawler.urls import normalize_host

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_CRAWLERS_DIR = Path(config.crawlers_dir())
_TARGET_ITEMS = 30  # positions-axis saturation for the §6 graded scalar (target_n)


def generate_command(args: argparse.Namespace) -> int:
    targets = _select(load_sites(config.sites_path()), args)
    if not targets:
        print("no matching sites to generate")
        return 1
    return asyncio.run(_run(targets))


def regenerate_one(slug: str) -> RegenOutcome:
    """Regenerate one site's crawler, reporting how it was delivered (§4.8, §14.2).

    The self-healing entry point — the scheduler's regen timer calls this for a
    confirmed-stale site. Runs the full §6 generation for that one site, then honors
    ``JOBCRAWLER_REVIEW_GATE`` (on by default): a gate-passing crawler is *staged* to
    crawlers/_pending/<slug>.py for human promotion rather than overwriting the live
    crawler the daemon is running. With the gate off it goes live (auto-heal). A
    missing site or an exhausted generation reports ``FAILED``.
    """
    site = find_site(load_sites(config.sites_path()), slug)
    if site is None:
        return RegenOutcome.FAILED
    root_lm, sub_lm = _build_lms()
    return asyncio.run(_generate_one(site, root_lm, sub_lm, mode=config.review_gate()))


def _build_lms() -> tuple[dspy.LM, dspy.LM]:
    """The §4.2 LM pair: a strong root planner + the cheap, host-capped ``sub_lm``.

    ``require_sub_model()`` errors when ``JOBCRAWLER_SUB_MODEL`` is unset rather than
    silently billing every in-sandbox ``llm_query`` at the root planner's rate, and
    ``CappedLM`` adds the unconditional sub-call kill (the only hard stop on a
    catch-and-continue loop). Both caps are host-side, never seen by the generator.
    """
    max_tokens = config.max_tokens()
    # build_lm routes a chatgpt/* slug to the Codex Responses backend (§9); an
    # openrouter/... slug is constructed exactly as before. CappedLM rides through the
    # same routing via cls= so the §4.2 sub-call kill still wraps the sub_lm.
    root_lm = build_lm(config.require_model(), max_tokens=max_tokens)
    sub_lm = build_lm(
        config.require_sub_model(),
        cls=CappedLM,
        max_subcalls=config.max_subcalls(),
        max_tokens=max_tokens,
    )
    return root_lm, sub_lm


def _committed_slugs(crawlers_dir: Path = _CRAWLERS_DIR) -> set[str]:
    """Slugs with a live committed crawler on disk (``crawlers/<slug>.py``).

    Only top-level modules count — the ``_pending``/``_history`` staging dirs and any
    ``_``-prefixed helper (e.g. ``__init__.py``) are not crawlers, so a site with only a
    staged candidate still reads as missing and ``--missing`` will (re-)generate it.
    """
    if not crawlers_dir.is_dir():
        return set()
    return {p.stem for p in crawlers_dir.glob("*.py") if not p.stem.startswith("_")}


def _select(sites: list[Site], args: argparse.Namespace) -> list[Site]:
    if args.all:
        return [s for s in sites if s.enabled]
    if getattr(args, "missing", False):
        have = _committed_slugs()
        missing = [s for s in sites if s.enabled and s.slug not in have]
        print(
            f"{len(missing)} enabled site(s) without a committed crawler "
            f"({len(have)} already have one)"
        )
        return missing
    if args.stale:
        print("--stale is driven by `jobcrawl run`'s health loop (§4.8)")
        return []
    site = find_site(sites, args.slug)
    return [site] if site is not None else []


async def _run(targets: list[Site]) -> int:
    root_lm, sub_lm = _build_lms()
    failures = 0
    for site in targets:
        # Operator-run generate writes live (stage defaults off); only a generation
        # that produced no gate-passing crawler counts as a failure.
        if await _generate_one(site, root_lm, sub_lm) is RegenOutcome.FAILED:
            failures += 1
    return 1 if failures else 0


async def _generate_one(
    site: Site, root_lm: dspy.LM, sub_lm: dspy.LM, *, mode: str = "off"
) -> RegenOutcome:
    registry_hosts = {normalize_host(site.url)}
    boot = Ctx(registry_hosts)
    corpus = prefetch_corpus(site.url, boot)
    # §8 inferred-host envelope: hosts the site's own served bytes vouch for (and
    # production politeness could actually fetch) join the *generation* allowlist,
    # so the RLM can explore a cross-host board API. Exploration ≠ admission: what
    # the winner actually declares is gate-1(b)'s job, judged against this same
    # evidence — and the committed crawl runs on registry + declared only.
    observed = corpus_host_evidence(corpus, site.url)
    evidence = _eligible_extra_hosts(observed)
    hosts = registry_hosts | evidence
    # Same page budget as the old single-ctx flow: the boot ctx's corpus spend
    # carries over rather than resetting.
    ctx = Ctx(hosts, max_pages=max(0, config.max_pages() - boot.pages_fetched))
    # Boot-load the §4.11-optimized strategy if one is pinned, else baseline; the
    # live tools/sub_lm/caps are rebuilt fresh and only the strategy state reloads.
    generator = load_generator(
        tools=make_host_tools(
            ctx, hosts, registry_hosts=registry_hosts, evidence_hosts=evidence
        ),
        sub_lm=sub_lm,
        max_iterations=config.max_iterations(),
        max_llm_calls=config.max_llm_calls(),
    )

    inputs: dict[str, object] = {
        "site_url": site.url,
        "corpus": corpus,
        "render": site.render,
        "hints": site.hints,
    }

    def core(source: str, carrier: object) -> CandidateResult:
        # Admission-shaped (§8): grade under the allowlist the committed crawler
        # will run with (registry + this candidate's evidence-backed EXTRA_HOSTS),
        # not the exploration envelope — otherwise a cross-host candidate that
        # never declares its API host scores full marks and wins the fan-out,
        # only to die at gate 1(b). Mirrors cli_optimize._candidate_hosts.
        return validate_crawler(
            source,
            allowed_hosts=admissible_hosts(
                source, registry_hosts=registry_hosts, evidence_hosts=evidence
            ),
        )

    reward_fn = make_reward_fn(
        core,
        target_n=_TARGET_ITEMS,
        min_description_chars=config.min_description_chars(),
        min_unique_chars=config.min_unique_description_chars(),
    )

    async def make_attempt(i: int) -> object:
        # track_usage so each candidate's $ cost is readable for the §4.2 fan-out cap.
        with dspy.context(
            lm=root_lm.copy(rollout_id=i, temperature=1.0), track_usage=True
        ):
            return await generator.aforward(
                site_url=site.url,
                corpus=corpus,
                render=site.render,
                hints=site.hints,
            )

    best, score = await best_of_n(
        make_attempt,
        reward=lambda pred: reward_fn(inputs, pred),
        n=config.fanout_n(),
        threshold=1.0,
        cost=_pred_cost,  # aggregate FANOUT_SPEND_CAP across the fan-out (§4.2)
        spend_cap=config.fanout_spend_cap_usd(),
        retries=config.fanout_retries(),
    )
    if best is None:
        print(f"FAIL {site.slug}: no candidate produced")
        return RegenOutcome.FAILED

    source = _source_text(best)
    # The enumeration *count* floor is NOT applied here: the 10s validation sandbox
    # fetches only a handful of pages, far fewer than a site's full recent set, so a
    # count gate would reject every candidate (and can't tell feed-only from a
    # sitemap crawler — both fetch ~the same few in the window). The generator is
    # instead steered toward full enumeration by site_features (it sees
    # expected_recent) and the must-observe "consult the sitemap" gate; the
    # count-based floor belongs at runtime, over a full crawl.
    ok, reason = gate_winner(
        best, source, registry_hosts, extra_evidence=evidence, url_evidence=observed
    )
    if not ok:
        print(f"FAIL {site.slug}: {reason}")
        return RegenOutcome.FAILED
    outcome = _deliver(site.slug, source, mode=mode)
    if outcome is RegenOutcome.STAGED:
        print(
            f"OK {site.slug}: staged crawlers/_pending/{site.slug}.py (score "
            f"{score:.2f}) — run `jobcrawl promote {site.slug}` to go live (§14.2)"
        )
    elif mode == "auto":
        print(
            f"OK {site.slug}: auto-promoted crawlers/{site.slug}.py (score "
            f"{score:.2f}); prior crawler backed up — "
            f"`jobcrawl rollback {site.slug}` to revert (§4.8)"
        )
    else:
        print(f"OK {site.slug}: wrote crawlers/{site.slug}.py (score {score:.2f})")
    _record_for_trainset(
        site,
        ctx,
        corpus,
        best,
        source,
        _committed_hosts(source, registry_hosts),
        url_evidence=observed,
    )
    return outcome


# Robots probes per generation. Platform-implied hosts rank first in the evidence
# list, so the bound trims noise (social/CDN links a homepage names), never the
# high-signal API-host candidates.
_EVIDENCE_PROBE_BOUND = 8


def _eligible_extra_hosts(candidates: list[str]) -> set[str]:
    """Filter host evidence to what production politeness could actually fetch (§4.5).

    The generation sandbox runs a bare ctx (no robots), but the scheduled crawl
    runs the polite one — a host whose robots.txt blanket-refuses us
    (api.getro.com is ``Disallow: /`` for every agent) would pass generation and
    then die on its first production crawl, wedging the site in a heal loop. So
    each candidate's robots.txt is probed up front and the forbidden are dropped
    from evidence entirely: not explorable, not declarable. Unreachable robots
    fails open, mirroring :class:`RobotsPolicy` in production.
    """
    policy = RobotsPolicy(_robots_fetch_text, user_agent=config.user_agent())
    return {
        host
        for host in candidates[:_EVIDENCE_PROBE_BOUND]
        if policy.allowed(f"https://{host}/")
    }


def _robots_fetch_text(url: str) -> str | None:
    """SSRF-checked robots.txt fetch for the evidence probe (fail-open on non-200)."""
    check_url(url, {normalize_host(url)})
    resp = httpx.get(url, timeout=10.0, headers={"user-agent": config.user_agent()})
    return resp.text if resp.status_code == 200 else None


def _committed_hosts(source: str, registry_hosts: set[str]) -> set[str]:
    """The allowlist the committed crawler will actually run under (§8).

    Registry host + declared EXTRA_HOSTS — the same derivation the crawl-time
    call sites make from the committed source (``contract.allowed_hosts_of``;
    gate-1(b) has already pinned SITE to the registry host and bound every
    declaration to evidence, so the two derivations coincide for anything that
    got this far). Gating and trainset recording validate against *this*, never
    the wider exploration envelope, so a fetch production would refuse fails at
    admission, not at the first scheduled crawl.
    """
    declared = {
        h
        for h in (normalize_host(f"https://{e}") for e in declared_extra_hosts(source))
        if h
    }
    return registry_hosts | declared


def _deliver(
    slug: str, source: str, *, mode: str, crawlers_dir: Path = _CRAWLERS_DIR
) -> RegenOutcome:
    """Write a gate-passing crawler per the §14.2 review-gate ``mode`` and report it.

    ``mode`` is the tri-state ``JOBCRAWLER_REVIEW_GATE`` (see ``config.review_gate``):

      * ``on`` — stage to ``crawlers/_pending/<slug>.py`` for a human to ``promote``;
        the live crawler the daemon runs is left untouched (→ STAGED → pending-review).
      * ``auto`` — back up the current live crawler to ``crawlers/_history/<slug>/``
        then overwrite it live, so an unattended §4.8 self-heal is still reversible via
        ``rollback_one`` (→ PROMOTED). The §6 gates already proved the new crawler;
        the dark incumbent (0 items, which is why the site is stale) it trivially beats.
      * ``off`` — write live with no backup (operator-trusted; → PROMOTED).

    The crawlers dir is created if absent: a relocated ``JOBCRAWLER_CRAWLERS_DIR``
    (e.g. a fresh ``/data/crawlers`` volume) won't exist on first delivery, and a live
    write must not ``FileNotFoundError`` on it.
    """
    crawlers_dir.mkdir(parents=True, exist_ok=True)
    if mode == "on":
        pending = crawlers_dir / "_pending"
        pending.mkdir(parents=True, exist_ok=True)
        (pending / f"{slug}.py").write_text(source)
        return RegenOutcome.STAGED
    if mode == "auto":
        _backup_live(slug, crawlers_dir)  # snapshot the incumbent so rollback can undo
    (crawlers_dir / f"{slug}.py").write_text(source)
    return RegenOutcome.PROMOTED


def _backup_live(slug: str, crawlers_dir: Path) -> Path | None:
    """Snapshot the current live crawler into a versioned ``_history/<slug>/`` copy.

    Called before an ``auto`` self-heal overwrites the live crawler so the prior version
    can be restored by :func:`rollback_one`. Snapshots use zero-padded, monotonically
    increasing indices, so they sort chronologically and a rollback walks back through
    them. Returns the snapshot path, or None when no live crawler exists yet (the
    first-ever delivery — there is nothing to preserve).
    """
    live = crawlers_dir / f"{slug}.py"
    if not live.exists():
        return None
    history = crawlers_dir / "_history" / slug
    history.mkdir(parents=True, exist_ok=True)
    existing = [int(p.stem) for p in history.glob("*.py") if p.stem.isdigit()]
    nxt = (max(existing) + 1) if existing else 0
    dest = history / f"{nxt:04d}.py"
    dest.write_text(live.read_text())
    return dest


def promote_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> tuple[bool, str]:
    """Promote a staged crawler to live (the human half of the §14.2 review gate).

    Moves ``crawlers/_pending/<slug>.py`` over ``crawlers/<slug>.py`` and consumes the
    staged copy. Returns ``(ok, message)``; ``ok`` is False (and nothing is written) if
    no crawler is staged for the slug.
    """
    pending = crawlers_dir / "_pending" / f"{slug}.py"
    if not pending.exists():
        return False, f"no staged crawler for {slug!r} (nothing in crawlers/_pending/)"
    (crawlers_dir / f"{slug}.py").write_text(pending.read_text())
    pending.unlink()
    return True, f"promoted {slug}: crawlers/_pending/{slug}.py → crawlers/{slug}.py"


def rollback_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> tuple[bool, str]:
    """Restore the most recent ``_history/<slug>/`` snapshot over the live crawler.

    The undo for an ``auto`` self-heal promotion (§4.8): it restores the newest backup
    and consumes it, so repeated rollbacks walk back through prior versions. Returns
    ``(ok, message)``; ``ok`` is False (nothing written) when no snapshot exists.
    """
    history = crawlers_dir / "_history" / slug
    snapshots = sorted(history.glob("*.py")) if history.exists() else []
    if not snapshots:
        return (
            False,
            f"no backup to roll back to for {slug!r} "
            f"(crawlers/_history/{slug}/ is empty)",
        )
    latest = snapshots[-1]
    (crawlers_dir / f"{slug}.py").write_text(latest.read_text())
    latest.unlink()  # consume so a second rollback goes one version further back
    return (
        True,
        f"rolled back {slug}: restored crawlers/{slug}.py "
        f"from _history/{slug}/{latest.name}",
    )


def gate_winner(
    pred: object,
    source: str,
    hosts: set[str],
    *,
    extra_evidence: Collection[str] = (),
    url_evidence: Collection[str] = (),
    expected_items: int | None = None,
) -> tuple[bool, str]:
    """The full §6 gate set on the selected winner; returns (passed, reason).

    ``hosts`` is the *registry* host set (§4.1); ``extra_evidence`` is the
    observed-host evidence the §8 host-admission gate judges any declared
    EXTRA_HOSTS against — empty means no extra host is admissible. The runtime
    gates (completeness, stability) validate against exactly the allowlist the
    committed crawler will run under (registry + admitted declarations), never a
    wider exploration envelope, so a fetch production would refuse fails here.

    ``url_evidence`` widens only the completeness gate's *sample-url* domain
    check: a Job's canonical link may live on any host the site's own served
    bytes name (§8 evidence rule — Greenhouse's ``absolute_url`` points at the
    human-facing job-boards host, which the crawler never fetches). Unlike
    ``extra_evidence`` it is not robots-filtered — robots governs fetching, not
    linking.

    ``expected_items`` (the site's open-posting count, from its listing/sitemap)
    activates the enumeration floor — a feed-sized sample of a far larger board is
    rejected as under-enumeration (§6).
    """
    ast_result = check_ast(source)
    if not ast_result.passed:
        return False, f"AST gate: {ast_result.reason}"

    admission = check_host_admission(
        source,
        registry_hosts=hosts,
        evidence_hosts=extra_evidence,
        cap=config.extra_hosts_cap(),
    )
    if not admission.passed:
        return False, f"host admission: {admission.reason}"
    run_hosts = _committed_hosts(source, hosts)

    must_observe = must_observe_failure(
        validated=_called(pred, "validate_crawler"),
        discovery_method=_discovery(pred),
        consulted_discovery=_called(pred, "find_feeds") or _called(pred, "get_sitemap"),
    )
    if must_observe is not None:
        return False, f"must-observe: {must_observe}"

    first = validate_crawler(source, allowed_hosts=run_hosts)
    completeness = check_completeness(
        first,
        min_items=config.min_items(),
        min_description_chars=config.min_description_chars(),
        allowed_hosts=run_hosts | set(url_evidence),
        expected_items=expected_items,
        enum_floor_ratio=config.enum_floor_ratio(),
        min_description_quality=config.min_description_quality(),
        min_unique_chars=config.min_unique_description_chars(),
    )
    if not completeness.passed:
        return False, f"completeness: {completeness.reason}"

    second = validate_crawler(source, allowed_hosts=run_hosts)
    verdict, jaccard = stability_verdict(
        _sample_urls(first), _sample_urls(second), threshold=config.stability_jaccard()
    )
    if verdict is StabilityVerdict.REJECT:
        return False, f"stability: near-random across two runs (jaccard {jaccard:.2f})"

    return True, "ok"


def _pred_cost(pred: object) -> float:
    """Best-effort USD cost of one candidate from dspy's LM-usage tracking.

    Returns 0.0 when no usage/cost signal is available (e.g. a provider litellm has
    no cost map for) — the §4.2 fan-out is then bounded structurally by ``fanout_n``
    and each candidate by the per-trajectory ``MAX_SUBCALLS`` kill, never by a
    fabricated number.
    """
    getter = getattr(pred, "get_lm_usage", None)
    if not callable(getter):
        return 0.0
    try:
        usage = getter() or {}
    except Exception:
        return 0.0
    if not isinstance(usage, dict):
        return 0.0
    total = 0.0
    for entry in usage.values():
        if isinstance(entry, dict) and isinstance(entry.get("cost"), (int, float)):
            total += float(entry["cost"])
    return total


def _sample_urls(result: CandidateResult) -> list[str]:
    return [str(sample.get("url", "")) for sample in result.samples]


def _called(pred: object, tool: str) -> bool:
    return tool in str(getattr(pred, "trajectory", ""))


def _discovery(pred: object) -> str:
    return str(getattr(pred, "discovery_method", "scrape"))


def _source_text(pred: object) -> str:
    src = getattr(pred, "crawler_source", "")
    code = getattr(src, "code", None)
    return code if isinstance(code, str) else str(src)


def _record_for_trainset(
    site: Site,
    ctx: Ctx,
    corpus: dict[str, object],
    pred: object,
    source: str,
    hosts: set[str],
    url_evidence: Collection[str] = (),
) -> None:
    """Append an admitted example to the §4.11 trainset — only if the §8 adversarial
    cross-validation over the *actual* items also passes (the §6 gates already did).
    The per-site stability admission is applied later, at optimize time.

    ``url_evidence`` widens only the cross-validation's *sample-url* domain check,
    the same evidence rule as gate 2 — Greenhouse's ``absolute_url`` lives on a
    corpus-evidenced sibling host, not the crawl host. Sandbox *execution* keeps
    exactly the committed allowlist.
    """
    result = validate_crawler(source, allowed_hosts=hosts)
    verdict = cross_validate(
        result.samples,
        live_titles=_live_titles(ctx, result.samples),
        allowed_hosts=hosts | set(url_evidence),
    )
    if not verdict.passed:
        print(f"  not recorded for trainset: adversarial failed ({verdict.reason})")
        return
    append_record(
        config.trainset_path(),
        slug=site.slug,
        example=make_example(
            site_url=site.url,
            corpus=corpus,
            render=site.render,
            hints=site.hints,
            discovery_method=_discovery(pred),
            discovery_url=str(getattr(pred, "discovery_url", "") or ""),
            crawler_source=source,
        ),
    )
    print(f"  recorded for §4.11 trainset -> {config.trainset_path()}")


def _live_titles(ctx: Ctx, samples: list[dict]) -> dict[str, str]:
    """Best-effort live ``<title>`` per sample url, for the §8 title-match check."""
    titles: dict[str, str] = {}
    for sample in samples:
        url = str(sample.get("url") or "")
        if not url:
            continue
        try:
            match = _TITLE_RE.search(ctx.fetch(url))
        except Exception:
            continue
        if match:
            titles[url] = re.sub(r"\s+", " ", match.group(1)).strip()
    return titles
