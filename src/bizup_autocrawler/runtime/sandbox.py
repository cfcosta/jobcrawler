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

The hardened browser/solver tiers (render, Cloudflare) are separate — ``ctx``
escalates to them transparently (§4.10); see ``solver.py``.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping

from bizup_autocrawler.generate.gates import check_ast
from bizup_autocrawler.generate.scoring import CandidateResult, ExceptionInfo

_ENTRY = "bizup_autocrawler.runtime._sandbox_entry"


def validate_crawler(
    source: str,
    *,
    allowed_hosts: Collection[str],
    replay: Mapping[str, Mapping[str, object]] | None = None,
    timeout: float = 10.0,
    mem_mb: int = 1024,
) -> CandidateResult:
    """Run one candidate crawler in an isolated subprocess → ``CandidateResult``."""
    ast_result = check_ast(source)
    if not ast_result.passed:
        return _failed("ASTGateError", ast_result.reason)

    payload = json.dumps(
        {"source": source, "allowed_hosts": list(allowed_hosts), "replay": replay}
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
