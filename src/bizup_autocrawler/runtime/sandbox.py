"""Sandboxed execution of generated crawlers — ``validate_crawler`` (§4.4, §8).

Executing model-written code against the live web is the central risk, so a
candidate runs in an isolated child process: AST-gated first (cheap, in-parent),
then exec'd in a subprocess with CPU + memory rlimits and a hard wall-clock
timeout. Network egress can only go through the ``ctx`` the child is handed — the
AST allowlist already bans ``socket``/``os``/``open`` *and* ``httpx``, so a
candidate cannot open its own connection. The child returns a **size-bounded**
``CandidateResult`` as JSON (capped samples, truncated bodies, exception =
type + message + traceback tail) so it survives RLM's output truncation.

``replay`` serves recorded HTTP to the child for the offline metric (§4.11) so it
never touches the network; omit it for live generation against the real site.

The same isolation guards *committed* crawls on the production schedule:
``collect_crawler`` reuses the AST gate + rlimits + wall-clock machinery but emits
**full** items (not bounded samples) so the host runner can dedup + ingest them —
a runaway committed crawler (infinite loop, memory blowup) is bounded and reported
rather than allowed to wedge or OOM the daemon.

The hardened browser/solver tiers (render, Cloudflare) are separate — ``ctx``
escalates to them transparently (§4.10); see ``solver.py``.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from bizup_autocrawler.generate.gates import check_ast
from bizup_autocrawler.generate.scoring import CandidateResult, ExceptionInfo
from bizup_autocrawler.runtime.contract import Item

_ENTRY = "bizup_autocrawler.runtime._sandbox_entry"
_COLLECT_ENTRY = "bizup_autocrawler.runtime._collect_entry"


def validate_crawler(
    source: str,
    *,
    allowed_hosts: Collection[str],
    replay: Mapping[str, Mapping[str, object]] | None = None,
    timeout: float = 10.0,
    mem_mb: int = 1024,
    polite: bool = False,
) -> CandidateResult:
    """Run one candidate crawler in an isolated subprocess → ``CandidateResult``.

    ``polite`` only bites on the live path (``replay is None``): when set, the child
    builds the §4.5-polite production ctx (per-host pacer + robots) via the same
    ``build_live_ctx`` factory the scheduled crawl uses, so an ``autocrawl validate``
    dry-run mirrors exactly what production will fetch. It defaults off so offline
    generation/scoring keeps a bare ctx and is not reshaped by robots/pacing.
    """
    ast_result = check_ast(source)
    if not ast_result.passed:
        return _failed("ASTGateError", ast_result.reason)

    payload = json.dumps(
        {
            "source": source,
            "allowed_hosts": list(allowed_hosts),
            "replay": replay,
            "polite": polite,
        }
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", _ENTRY],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_limits(mem_mb, int(timeout) + 1),
        )
    except subprocess.TimeoutExpired:
        return _failed("TimeoutError", f"crawler exceeded {timeout}s wall clock")

    if proc.returncode != 0:
        return _failed(
            "SandboxError", f"child exited {proc.returncode}", tail=proc.stderr[-2000:]
        )
    return CandidateResult.model_validate_json(proc.stdout)


def _failed(kind: str, message: str, *, tail: str = "") -> CandidateResult:
    return CandidateResult(
        item_count=0,
        pages_fetched=0,
        exception=ExceptionInfo(type=kind, message=message, traceback_tail=tail),
    )


def _apply_limits(mem_mb: int, cpu_seconds: int) -> Callable[[], None]:
    def preexec() -> None:
        cap = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    return preexec


# -- committed-crawl isolation (§4.4): full items, not bounded samples --------


@dataclass(frozen=True, slots=True)
class CollectResult:
    """The result of running a committed crawler in the §4.4 subprocess.

    Unlike :class:`CandidateResult` (size-bounded for the scorer), this carries
    **every** validated :class:`Item` so the host can dedup + ingest them.
    ``errors`` counts contract-violating yields; ``exception`` is a top-level
    crash / timeout / gate rejection (the host folds it into the run's error count).
    """

    items: list[Item]
    errors: int
    pages_fetched: int
    exception: ExceptionInfo | None = None


def collect_crawler(
    source: str,
    *,
    allowed_hosts: Collection[str],
    timeout: float = 600.0,
    mem_mb: int = 1024,
    cpu_seconds: float = 120.0,
) -> CollectResult:
    """Run a *committed* crawler in the §4.4 subprocess, returning FULL items.

    Same isolation as :func:`validate_crawler` (AST gate → RLIMIT_AS / RLIMIT_CPU →
    wall-clock timeout) but the child emits every item in full so the host can dedup
    + ingest them. RLIMIT_CPU is decoupled from the wall-clock ``timeout``: a polite
    crawl spends most of its wall-clock asleep between paced fetches, so the CPU cap
    stays tight (kills a compute spin) while the wall-clock budget stays generous
    (covers legitimately slow, paced crawls). Never raises — a hang, crash, or gate
    rejection comes back as ``exception`` so a single bad crawler can't abort the
    fleet (§4.4 failure isolation)."""
    ast_result = check_ast(source)
    if not ast_result.passed:
        return _collect_failed("ASTGateError", ast_result.reason)

    payload = json.dumps({"source": source, "allowed_hosts": list(allowed_hosts)})
    try:
        proc = subprocess.run(
            [sys.executable, "-m", _COLLECT_ENTRY],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_limits(mem_mb, int(cpu_seconds) + 1),
        )
    except subprocess.TimeoutExpired:
        return _collect_failed(
            "TimeoutError", f"crawler exceeded {timeout}s wall clock"
        )

    if proc.returncode != 0:
        # A non-zero exit is the kernel killing the child on a breached rlimit
        # (RLIMIT_AS / RLIMIT_CPU) or an interpreter-level crash — report, don't raise.
        return _collect_failed(
            "SandboxError", f"child exited {proc.returncode}", tail=proc.stderr[-2000:]
        )
    return _parse_collect(proc.stdout)


def _collect_failed(kind: str, message: str, *, tail: str = "") -> CollectResult:
    return CollectResult(
        items=[],
        errors=0,
        pages_fetched=0,
        exception=ExceptionInfo(type=kind, message=message, traceback_tail=tail),
    )


def _parse_collect(stdout: str) -> CollectResult:
    data = json.loads(stdout)
    items: list[Item] = []
    errors = int(data.get("errors", 0))
    for raw in data.get("items", []):
        try:
            items.append(Item.model_validate(raw))
        except Exception:  # a malformed item dump from the child (should not happen)
            errors += 1
    exc = data.get("exception")
    exception = ExceptionInfo(**exc) if exc else None
    return CollectResult(
        items=items,
        errors=errors,
        pages_fetched=int(data.get("pages_fetched", 0)),
        exception=exception,
    )
