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
import os
import re
import tempfile
from collections.abc import Collection, Mapping
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
    is_ats_backend,
    must_observe_failure,
    stability_verdict,
)
from jobcrawler.generate.adversarial import cross_validate
from jobcrawler.generate.budget import CappedLM
from jobcrawler.generate.lm_capture import begin_capture, capture_callback, end_capture
from jobcrawler.lm import build_lm, prediction_usage_rows, run_llm_loop
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
from jobcrawler.state import StateStore
from jobcrawler.trainset import append_record, make_example
from jobcrawler.urls import host_on_domain, normalize_host

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_CRAWLERS_DIR = Path(config.crawlers_dir())
_TARGET_ITEMS = 30  # positions-axis saturation for the §6 graded scalar (target_n)


def generate_command(args: argparse.Namespace) -> int:
    targets = _select(load_sites(config.sites_path()), args)
    if not targets:
        print("no matching sites to generate")
        return 1
    store = (
        _open_store()
    )  # record each attempt's outcome + LLM log to generation_log (§4.2)
    try:
        return run_llm_loop(_run(targets, store))
    finally:
        store.close()


def regenerate_one(slug: str, *, store: StateStore | None = None) -> RegenOutcome:
    """Regenerate one site's crawler, reporting how it was delivered (§4.8, §14.2).

    The self-healing entry point — the scheduler's regen timer calls this for a
    confirmed-stale site. Runs the full §6 generation for that one site, then honors
    ``JOBCRAWLER_REVIEW_GATE`` (on by default): a gate-passing crawler is *staged* to
    crawlers/_pending/<slug>.py for human promotion rather than overwriting the live
    crawler the daemon is running. With the gate off it goes live (auto-heal). A
    missing site or an exhausted generation reports ``FAILED``.

    ``store`` (the daemon's, passed by the self-healer) persists the attempt's outcome
    and captured LLM log to ``generation_log`` (§4.2); ``None`` skips recording.
    """
    site = find_site(load_sites(config.sites_path()), slug)
    if site is None:
        return RegenOutcome.FAILED
    root_lm, sub_lm = _build_lms()
    return run_llm_loop(
        _generate_one(site, root_lm, sub_lm, mode=config.review_gate(), store=store)
    )


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
    # Attach the §4.2 LLM-log capture to both LMs (and, via dspy's deep-copying copy(),
    # to every per-candidate rollout copy): each call routes into the per-attempt buffer
    # begin_capture() activates. A no-op for any LM used outside a generation attempt.
    capture = [capture_callback()]
    root_lm = build_lm(config.require_model(), max_tokens=max_tokens, callbacks=capture)
    sub_lm = build_lm(
        config.require_sub_model(),
        cls=CappedLM,
        max_subcalls=config.max_subcalls(),
        max_tokens=max_tokens,
        callbacks=capture,
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


async def _run(targets: list[Site], store: StateStore | None = None) -> int:
    root_lm, sub_lm = _build_lms()
    failures = 0
    for site in targets:
        # Operator-run generate writes live (stage defaults off); only a generation
        # that produced no gate-passing crawler counts as a failure.
        if (
            await _generate_one(site, root_lm, sub_lm, store=store)
            is RegenOutcome.FAILED
        ):
            failures += 1
    return 1 if failures else 0


async def _generate_one(
    site: Site,
    root_lm: dspy.LM,
    sub_lm: dspy.LM,
    *,
    mode: str = "off",
    store: StateStore | None = None,
) -> RegenOutcome:
    # §4.2 generation is LLM-gated, and the daemon's stdout dies with its terminal, so
    # (when a store is wired) capture the attempt's LLM interaction log and persist the
    # outcome once at the end — success or failure, even on an unexpected raise. The
    # capture is thread-scoped, so concurrent self-heals don't cross-contaminate.
    if store is not None:
        begin_capture()
    result = RegenOutcome.FAILED
    attempt_reason = "generation did not complete"
    attempt_score: float | None = None
    usage_acc: list[tuple[str, int, int]] = []  # per-candidate (model, in, out) rows
    try:
        result, attempt_reason, attempt_score = await _run_generation(
            site, root_lm, sub_lm, mode=mode, usage=usage_acc
        )
        return result
    except Exception as exc:  # enrich the record, then let the healer see the failure
        attempt_reason = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        if store is not None:
            _persist_attempt(
                store, site.slug, result, attempt_reason, attempt_score, end_capture()
            )
            _persist_usage(store, usage_acc)


async def _run_generation(
    site: Site,
    root_lm: dspy.LM,
    sub_lm: dspy.LM,
    *,
    mode: str,
    usage: list[tuple[str, int, int]] | None = None,
) -> tuple[RegenOutcome, str, float | None]:
    """Run one §6 generation and report ``(outcome, reason, score)`` for the attempt record.

    Split from :func:`_generate_one` so the capture/persist wrapper stays a thin,
    single-exit shell: this returns the structured triple at every decision point instead
    of leaving the "why" only in the ``print`` lines.

    ``usage`` (when given) accumulates each candidate's per-model token rows — root and
    ``sub_lm`` land under their own model slugs — so the caller can ledger the attempt's
    whole fan-out, not just the winner.
    """
    registry_hosts = {normalize_host(site.url)}
    boot = Ctx(registry_hosts)
    corpus = prefetch_corpus(site.url, boot)
    # §8 authorization envelope: beyond the registry host, a generated crawler may
    # fetch only the operator-declared ``Site.extra_hosts`` (the ATS/API host a
    # careers page is backed by), robots-permitting. These — never hosts merely
    # *named* in the fetched, attacker-influenceable page content — form the
    # *generation* allowlist the RLM explores AND the evidence gate-1(b) judges the
    # winner's declared EXTRA_HOSTS against, so a compromised/hostile page cannot
    # authorize the crawler to egress to a host it names. The committed crawl runs
    # on registry + declared only. Page-named-but-unauthorized hosts are surfaced as
    # an operator hint (add them to the registry entry if legitimate), not admitted.
    authorized = _authorized_extra_hosts(site)
    observed = corpus_host_evidence(corpus, site.url)
    _report_unauthorized_named_hosts(site.slug, observed, authorized, registry_hosts)
    hosts = registry_hosts | authorized
    # Same page budget as the old single-ctx flow: the boot ctx's corpus spend
    # carries over rather than resetting.
    ctx = Ctx(hosts, max_pages=max(0, config.max_pages() - boot.pages_fetched))
    # Boot-load the §4.11-optimized strategy if one is pinned, else baseline; the
    # live tools/sub_lm/caps are rebuilt fresh and only the strategy state reloads.
    generator = load_generator(
        tools=make_host_tools(
            ctx, hosts, registry_hosts=registry_hosts, evidence_hosts=authorized
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
        # will run with (registry + this candidate's operator-authorized EXTRA_HOSTS),
        # not the exploration envelope — otherwise a cross-host candidate that
        # never declares its API host scores full marks and wins the fan-out,
        # only to die at gate 1(b). Mirrors cli_optimize._candidate_hosts.
        return validate_crawler(
            source,
            allowed_hosts=admissible_hosts(
                source, registry_hosts=registry_hosts, evidence_hosts=authorized
            ),
        )

    reward_fn = make_reward_fn(
        core,
        target_n=_TARGET_ITEMS,
        min_description_chars=config.min_description_chars(),
        min_unique_chars=config.min_unique_description_chars(),
    )

    async def make_attempt(i: int) -> object:
        # track_usage so each candidate's token count is readable for the §4.2 fan-out cap.
        with dspy.context(
            lm=root_lm.copy(rollout_id=i, temperature=1.0), track_usage=True
        ):
            pred = await generator.aforward(
                site_url=site.url,
                corpus=corpus,
                render=site.render,
                hints=site.hints,
            )
        if usage is not None:  # ledger every candidate, not just best_of_n's winner
            usage.extend(prediction_usage_rows(pred))
        return pred

    best, score = await best_of_n(
        make_attempt,
        reward=lambda pred: reward_fn(inputs, pred),
        n=config.fanout_n(),
        threshold=1.0,
        cost=_pred_tokens,  # aggregate FANOUT_TOKEN_CAP across the fan-out (§4.2)
        spend_cap=float(config.fanout_token_cap()),
        retries=config.fanout_retries(),
    )
    if best is None:
        print(f"FAIL {site.slug}: no candidate produced")
        return RegenOutcome.FAILED, "no candidate produced", None

    source = _source_text(best)
    # The enumeration *count* floor is NOT applied here: the 10s validation sandbox
    # fetches only a handful of pages, far fewer than a site's full recent set, so a
    # count gate would reject every candidate (and can't tell feed-only from a
    # sitemap crawler — both fetch ~the same few in the window). The generator is
    # instead steered toward full enumeration by site_features (it sees
    # expected_recent) and the must-observe "consult the sitemap" gate; the
    # count-based floor belongs at runtime, over a full crawl.
    ok, reason = gate_winner(
        best,
        source,
        registry_hosts,
        extra_evidence=authorized,
        url_evidence=observed,
        expected_items=_empty_board_signal(corpus),
        discovery_prefetched=_snapshot_surfaced_discovery(corpus),
    )
    if not ok:
        print(f"FAIL {site.slug}: {reason}")
        return RegenOutcome.FAILED, reason, None
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
    return outcome, "ok", score


def _persist_attempt(
    store: StateStore,
    slug: str,
    outcome: RegenOutcome,
    reason: str,
    score: float | None,
    llm_log: list[dict[str, object]],
) -> None:
    """Write one generation attempt to the store; never raise into the generation path."""
    try:
        store.record_generation(
            slug,
            outcome=outcome.name.lower(),  # promoted | staged | failed
            reason=reason,
            score=score,
            llm_log=llm_log,
        )
    except Exception as exc:  # noqa: BLE001 - logging must not break a self-heal
        print(f"  generation log not persisted for {slug}: {exc}")


def _persist_usage(store: StateStore, rows: list[tuple[str, int, int]]) -> None:
    """Ledger one attempt's per-model token usage; never raise into the generation path.

    Rows are pre-summed per model so an attempt writes at most one ledger bump per LM
    (root + sub land individually) instead of one per candidate."""
    totals: dict[str, list[int]] = {}
    for model, tokens_in, tokens_out in rows:
        entry = totals.setdefault(model, [0, 0])
        entry[0] += tokens_in
        entry[1] += tokens_out
    try:
        for model, (tokens_in, tokens_out) in totals.items():
            store.bump_llm_usage(
                "generate", model, tokens_in=tokens_in, tokens_out=tokens_out
            )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not break a self-heal
        print(f"  llm usage not persisted: {exc}")


def _open_store() -> StateStore:
    """Open the shared §4.7 store from config (the operator ``generate`` path's own handle)."""
    url = config.state_url()
    db_path = url.removeprefix("sqlite:///") if url.startswith("sqlite:") else url
    return StateStore(db_path)


# Robots probes per generation — an upper bound on how many declared extra hosts an
# operator can authorize for one site, so a fat-fingered registry entry can't fan out
# into an unbounded robots-probe storm.
_EVIDENCE_PROBE_BOUND = 8


def _authorized_extra_hosts(site: Site) -> set[str]:
    """The operator-authorized extra fetch hosts for ``site`` (§8), robots-filtered.

    ``Site.extra_hosts`` normalized to bare hosts and passed through the same robots
    gate as any candidate host. This is the SOLE authorization source for a generated
    crawler's ``EXTRA_HOSTS``: a host present only in the fetched page content is never
    admitted here, so a hostile or compromised page cannot steer the crawler into
    fetching a host it merely names. An empty declaration (the default) keeps the
    crawler on its registry host alone.
    """
    declared = [
        h for h in (normalize_host(f"https://{e}") for e in site.extra_hosts) if h
    ]
    return _eligible_extra_hosts(declared)


def _report_unauthorized_named_hosts(
    slug: str,
    named: Collection[str],
    authorized: set[str],
    registry_hosts: set[str],
) -> None:
    """Print a §8 operator hint for hosts the page names but nothing authorizes.

    The generator no longer treats page-named hosts as fetch evidence, so a
    legitimate cross-host board (careers page served off a distinct ATS/API host)
    now needs an explicit ``extra_hosts`` entry. Surfacing the un-covered named
    hosts turns that from a silent gate-1(b) rejection into an actionable nudge —
    without ever admitting them.
    """
    candidates = {h for h in (normalize_host(f"https://{n}") for n in named) if h}
    unauthorized = sorted(
        h
        for h in candidates
        if not host_on_domain(h, registry_hosts)
        and not host_on_domain(h, authorized)
        and not is_ats_backend(h)
    )
    if unauthorized:
        print(
            f"  {slug}: page names host(s) not authorized to fetch: "
            f"{', '.join(unauthorized)} — add to the registry entry's extra_hosts "
            f"if a legitimate ATS/API backend (§8)"
        )


def _eligible_extra_hosts(candidates: list[str]) -> set[str]:
    """Filter candidate extra hosts to what production politeness could fetch (§4.5).

    The generation sandbox runs a bare ctx (no robots), but the scheduled crawl
    runs the polite one — a host whose robots.txt blanket-refuses us
    (api.getro.com is ``Disallow: /`` for every agent) would pass generation and
    then die on its first production crawl, wedging the site in a heal loop. So
    each candidate's robots.txt is probed up front and the forbidden are dropped
    entirely: not explorable, not declarable. Unreachable robots fails open,
    mirroring :class:`RobotsPolicy` in production.
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same dir + ``os.replace``.

    The live crawler file (``crawlers/<slug>.py``) is read by the crawl task, which since
    §4.4 runs concurrently with generation — bootstrap/regen deliver here from their own
    passes while a crawl loads a sibling. A bare ``Path.write_text`` truncates then rewrites
    in place, so a crawl importing the file mid-write could see a torn (syntactically
    invalid) module. ``os.replace`` swaps the inode atomically on POSIX, so a concurrent
    reader always opens either the whole old crawler or the whole new one, never a partial.

    The temp shares ``path``'s directory (not ``/tmp``) so the rename stays within one
    filesystem and can't fail with ``EXDEV``; its dotted, ``.tmp``-suffixed name keeps it
    out of any ``*.py`` crawler glob, and it is removed if the write fails before the swap.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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
    _atomic_write_text(crawlers_dir / f"{slug}.py", source)
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
    _atomic_write_text(crawlers_dir / f"{slug}.py", pending.read_text())
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
    _atomic_write_text(crawlers_dir / f"{slug}.py", latest.read_text())
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
    discovery_prefetched: bool = False,
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

    ``discovery_prefetched`` records that the snapshot already performed and surfaced
    feed/sitemap/API discovery (``_snapshot_surfaced_discovery``), which the gate 2(a)
    ``consulted_discovery`` evidence counts — see the must-observe block below.
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

    # ``consulted_discovery`` credits the harness's own prefetch, not just an in-trajectory
    # tool call: ``prefetch_corpus`` always probes homepage feeds + robots/jobs-sitemap +
    # ATS APIs and surfaces them in the ``corpus`` the model reads FROM, so a candidate that
    # read that and still chose ``scrape`` HAS observed the discovery surface — demanding a
    # redundant ``find_feeds``/``get_sitemap`` call only burned a full trajectory before this
    # deterministic reject. The enum-floor (``expected_items``) still guards a scrape that
    # under-enumerates a known-larger board; the validate-before-SUBMIT half is untouched.
    must_observe = must_observe_failure(
        validated=_called(pred, "validate_crawler"),
        discovery_method=_discovery(pred),
        consulted_discovery=(
            discovery_prefetched
            or _called(pred, "find_feeds")
            or _called(pred, "get_sitemap")
        ),
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


def _pred_tokens(pred: object) -> float:
    """Best-effort total tokens (prompt + completion) of one candidate from dspy usage.

    dspy's ``get_lm_usage()`` records per-LM *token counts* for every provider — never a
    ``cost`` key, and litellm can't price many of the slugs this runs on (OpenRouter /
    Codex) — so the §4.2 fan-out cap is denominated in tokens, not dollars. Sums
    ``total_tokens`` (or ``prompt_tokens + completion_tokens`` when only the components
    are present) across every LM a candidate touched (root + ``sub_lm``).

    Returns 0.0 when no usage signal is available — the fan-out is then bounded
    structurally by ``fanout_n`` and each candidate by the per-trajectory
    ``MAX_SUBCALLS`` kill, never by a fabricated number.
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
        if not isinstance(entry, dict):
            continue
        rolled_up = entry.get("total_tokens")
        if isinstance(rolled_up, (int, float)):
            total += float(rolled_up)
            continue
        # No rolled-up total on this entry: sum the components dspy does record.
        for key in ("prompt_tokens", "completion_tokens"):
            value = entry.get(key)
            if isinstance(value, (int, float)):
                total += float(value)
    return total


def _sample_urls(result: CandidateResult) -> list[str]:
    return [str(sample.get("url", "")) for sample in result.samples]


def _called(pred: object, tool: str) -> bool:
    return tool in str(getattr(pred, "trajectory", ""))


def _discovery(pred: object) -> str:
    return str(getattr(pred, "discovery_method", "scrape"))


def _snapshot_surfaced_discovery(corpus: object) -> bool:
    """Did the prefetched snapshot already perform feed/sitemap/API discovery? (§4.2)

    ``prefetch_corpus`` always probes homepage feeds, robots + jobs sitemap, and ATS API
    candidates, and ``build_corpus`` surfaces them (``feeds``/``sitemap``/``apis``) in the
    ``corpus`` the model reasons FROM — so the consultation gate 2(a) demands has already
    happened, in the model's hand, before it writes a line. A real prefetched corpus always
    carries those keys; a bare/hand-built one that skipped discovery does not, and gets no
    credit (the gate's strict in-trajectory check still applies)."""
    if not isinstance(corpus, Mapping):
        return False
    return {"feeds", "sitemap", "apis"} <= corpus.keys()


def _empty_board_signal(corpus: object) -> int | None:
    """``0`` iff the prefetched snapshot proved the board is *currently empty* (§6).

    Returns ``0`` only when the snapshot found real ATS API candidates yet its
    independent probe of them reported zero open roles (``expected_positions == 0``):
    a valid ATS board advertising nothing right now. That ``0`` is the empty-board
    admission signal :func:`check_completeness` keys off to promote a clean 0-yield
    crawler instead of hard-failing ``min_items`` and regenerating forever.

    Returns ``None`` (floor-dormant, the prior behavior) otherwise — including for a
    board that lists roles, whose probe returns a positive total, so a *broken*
    0-yield candidate there still fails the floor. Never returns a positive value, so
    the gen-time enumeration floor stays deliberately dormant (the runtime enforces
    it over a full crawl; see the ``gate_winner`` call site)."""
    if not isinstance(corpus, Mapping):
        return None
    apis = corpus.get("apis")
    if corpus.get("expected_positions") == 0 and isinstance(apis, list) and apis:
        return 0
    return None


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
