"""Sandboxed execution of generated crawlers — ``validate_crawler`` (§4.4, §8).

Executing model-written code against the live web is the central risk, so a
candidate runs in an isolated child process: AST-gated first (cheap, in-parent),
then exec'd in a subprocess with CPU + memory rlimits and a hard wall-clock
timeout. Network egress can only go through the ``ctx`` the child is handed — the
AST allowlist already bans ``socket``/``os``/``open`` *and* ``httpx``, so a
candidate cannot open its own connection. The child returns a **size-bounded**
``CandidateResult`` as JSON (capped sample count, pipe-bounded description text,
exception = type + message + traceback tail) — bounded against a pathological
candidate, but *faithful*: the §6 gate and §4.11 axes grade these exact strings.
The RLM-facing preview clip lives in the ``validate_crawler`` tool (§4.3), which
owns the bridge's output budget.

``replay`` serves recorded HTTP to the child for the offline metric (§4.11) so it
never touches the network; omit it for live generation against the real site.

The same isolation guards *committed* crawls on the production schedule:
``collect_crawler`` reuses the AST gate + rlimits + wall-clock machinery but emits
**full** jobs (not bounded samples) so the host runner can dedup + track them —
a runaway committed crawler (infinite loop, memory blowup) is bounded and reported
rather than allowed to wedge or OOM the daemon.

The hardened browser/solver tiers (render, Cloudflare) are separate — ``ctx``
escalates to them transparently (§4.10); see ``solver.py``.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from jobcrawler import config
from jobcrawler.generate.gates import check_ast
from jobcrawler.generate.scoring import CandidateResult, ExceptionInfo
from jobcrawler.runtime import egress
from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.ssrf import SsrfError, check_url

_ENTRY = "jobcrawler.runtime._sandbox_entry"
_COLLECT_ENTRY = "jobcrawler.runtime._collect_entry"


# -- child environment scrub (§8): the child never sees a provider secret ------
# The child runs untrusted, model-written crawler code. If it inherited the
# daemon's whole environment (the default when ``subprocess.run`` gets no
# ``env=``), a read primitive that slipped the AST + restricted-builtins gates
# could exfiltrate the provider API keys, the ChatGPT OAuth token-store path
# (``config.auth_dir`` → ``JOBCRAWLER_AUTH_DIR`` / ``CHATGPT_TOKEN_DIR``), and any
# other ambient secret. So the child is spawned with a *curated* env: exactly the
# crawl knobs its live-ctx path reads, plus the OS/interpreter vars it needs to
# boot and verify TLS — nothing else. This is the loot-removal half of the
# sandbox's defense in depth; the exfil-channel half is the egress allowlist.

# The JOBCRAWLER_* (+ FLARESOLVERR_URL) knobs the child's ``build_live_ctx`` / Ctx
# path actually reads (live_ctx.py, ctx.py, solver.py). Must stay COMPLETE: a knob
# omitted here silently reverts the child to that getter's default, so an operator
# override (a tighter rate limit, a longer timeout, the egress proxy) would be lost
# inside the sandbox. Deliberately excludes every model/provider knob — the child
# never talks to an LLM, so JOBCRAWLER_MODEL/keys and the OAuth dir have no business
# here even though they are JOBCRAWLER_*-shaped.
_CRAWL_ENV_KEYS = frozenset(
    {
        "JOBCRAWLER_USER_AGENT",
        "JOBCRAWLER_RATE_LIMIT",
        "JOBCRAWLER_MAX_PAGES",
        "JOBCRAWLER_FETCH_TIMEOUT",
        "JOBCRAWLER_FETCH_RETRIES",
        "JOBCRAWLER_FETCH_BACKOFF",
        "JOBCRAWLER_FETCH_RETRY_CAP",
        "JOBCRAWLER_SOLVER",
        "FLARESOLVERR_URL",
        "JOBCRAWLER_CLEARANCE_TTL",
        "JOBCRAWLER_EGRESS_PROXY",
    }
)

# OS / interpreter vars the child needs merely to *start* and import (it is
# ``python -m`` re-exec'ing the package) and to verify TLS. These carry no
# application secret — they are paths, locale, and CA-bundle pointers. Under
# ``nix develop`` the interpreter resolves its dependencies through PYTHONPATH and
# its CA bundle through NIX_SSL_CERT_FILE / LOCALE_ARCHIVE, so dropping them would
# stop the child from importing httpx/lxml or from verifying any HTTPS cert — i.e.
# break every production crawl. Passed through only when actually set in the parent.
_SYSTEM_ENV_KEYS = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "LD_LIBRARY_PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NIX_SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "LOCALE_ARCHIVE",
    }
)


def _child_env() -> dict[str, str]:
    """The curated environment for a sandbox child — crawl knobs + boot/TLS vars only.

    An *allowlist*, not a scrub-list: only the keys in :data:`_CRAWL_ENV_KEYS` and
    :data:`_SYSTEM_ENV_KEYS` that are present in the parent env are forwarded, so a
    provider API key or OAuth-dir pointer added to the daemon's environment later
    can never reach the untrusted child by default (§8). Callers pass this as
    ``env=`` to every crawler subprocess.

    Because the scrub drops whatever ambient var put the package on the path (an
    editable src layout, a nix dev-shell ``REPO_ROOT`` sitecustomize, a venv
    activation), it then hands the child an explicit ``PYTHONPATH`` naming the
    directory that CONTAINS the ``jobcrawler`` package — ``parents[2]`` of this
    file (``…/src`` in a checkout, ``…/site-packages`` once installed, harmless
    either way). Third-party deps already sit on the interpreter's own site-path,
    which is resolved from ``sys.executable`` and needs no env var. Without this
    the scrubbed child would fail to import the package and every crawl would come
    back as a ``SandboxError``.
    """
    allowed = _CRAWL_ENV_KEYS | _SYSTEM_ENV_KEYS
    env = {key: value for key, value in os.environ.items() if key in allowed}
    pkg_parent = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        pkg_parent if not existing else pkg_parent + os.pathsep + existing
    )
    return env


def _run_child(
    entry: str,
    payload: str,
    *,
    timeout: float,
    mem_mb: int,
    cpu_seconds: int,
    allowed_hosts: Collection[str],
    live: bool,
) -> tuple[int, str, str]:
    """Run a sandbox child; return ``(returncode, stderr, result_text)`` (§8).

    The result is read from a private temp file the child is told to write — its
    path is passed on argv — **not** from stdout. The child shares its stdout with
    the untrusted crawler (a bare ``print``, ``ctx.log``), so writing the result
    JSON there would let a posting that merely logged corrupt it, or a hostile
    candidate that prints a forged ``CandidateResult`` *spoof* it. So the crawler's
    stdout is discarded to ``DEVNULL`` (which also caps a print-flood memory DoS
    against the parent), while stderr is captured for the crash tail. Raises
    ``TimeoutExpired`` on a wall-clock breach — the caller maps it to a failure
    result — and always unlinks the temp file.

    On a **live** child (``live=True``) the subprocess is wrapped in an
    :class:`~jobcrawler.runtime.egress.EgressGuard` (#19b): the parent binds a
    per-crawl allowlisting loopback proxy, points the child's proxy env at it, and
    arms the strongest OS-enforcement rung available. Its env overrides are merged
    over the curated :func:`_child_env` (proxy vars win) and any handshake fds are
    passed through. In ``require`` mode with no real OS rung the guard raises
    :class:`~jobcrawler.runtime.egress.EgressRefused` here — the caller maps it to a
    ``SandboxError``-shaped result so the site is skipped (fail closed). The offline
    replay path (``live=False``) skips the guard entirely: it never touches the
    network (MockTransport ignores proxy env) and must not be refused.
    """
    fd, result_path = tempfile.mkstemp(prefix="jobcrawler-sandbox-", suffix=".json")
    os.close(fd)
    try:
        with egress.EgressGuard(
            allowed_hosts, enforce=config.egress_enforce()
        ) if live else _no_egress() as guard:
            if guard is not None and guard.refused is not None:
                raise egress.EgressRefused(guard.refused)
            env = _child_env()
            pass_fds: tuple[int, ...] = ()
            if guard is not None and guard.env:
                env.update(guard.env)  # proxy/arm vars win over the curated base
            if guard is not None:
                pass_fds = guard.pass_fds
            proc = subprocess.run(
                [sys.executable, "-m", entry, result_path],
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=env,
                pass_fds=pass_fds,
                preexec_fn=_apply_limits(mem_mb, cpu_seconds),
            )
        return proc.returncode, proc.stderr or "", Path(result_path).read_text(
            encoding="utf-8"
        )
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass


@contextmanager
def _no_egress() -> Iterator[None]:
    """The offline (replay) branch of ``_run_child``'s guard: a no-op context.

    Keeps the ``with`` uniform so the live and offline paths share one body; yields
    ``None`` so the guard checks below short-circuit."""
    yield None


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
    ``build_live_ctx`` factory the scheduled crawl uses, so an ``jobcrawl validate``
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
        returncode, stderr, result_text = _run_child(
            _ENTRY,
            payload,
            timeout=timeout,
            mem_mb=mem_mb,
            cpu_seconds=int(timeout) + 1,
            allowed_hosts=allowed_hosts,
            live=replay is None,
        )
    except subprocess.TimeoutExpired:
        return _failed("TimeoutError", f"crawler exceeded {timeout}s wall clock")
    except egress.EgressRefused as exc:
        # require-mode with no OS egress rung: skip the site rather than crawl it
        # uncontained (fail closed). Shaped like any other sandbox failure (§8/#19b).
        return _failed("SandboxError", f"egress refused: {exc}")

    if returncode != 0:
        return _failed(
            "SandboxError", f"child exited {returncode}", tail=stderr[-2000:]
        )
    try:
        return CandidateResult.model_validate_json(result_text)
    except Exception as exc:
        # Never raise into the fan-out: a truncated or empty result file (the child
        # died mid-write, or crashed before emitting) is a sandbox failure, not a
        # host crash. Fold it into a failed result like any other.
        return _failed(
            "SandboxError",
            f"unparseable child result: {exc}",
            tail=(stderr + "\n--- result ---\n" + result_text)[-2000:],
        )


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
    **every** validated :class:`Job` so the host can dedup + track them.
    ``errors`` counts contract-violating yields; ``exception`` is a top-level
    crash / timeout / gate rejection (the host folds it into the run's error count).
    """

    items: list[Job]
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
    """Run a *committed* crawler in the §4.4 subprocess, returning FULL jobs.

    Same isolation as :func:`validate_crawler` (AST gate → RLIMIT_AS / RLIMIT_CPU →
    wall-clock timeout) but the child emits every job in full so the host can dedup
    + track them. RLIMIT_CPU is decoupled from the wall-clock ``timeout``: a polite
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
        returncode, stderr, result_text = _run_child(
            _COLLECT_ENTRY,
            payload,
            timeout=timeout,
            mem_mb=mem_mb,
            cpu_seconds=int(cpu_seconds) + 1,
            allowed_hosts=allowed_hosts,
            live=True,  # a committed crawl always runs against the live site
        )
    except subprocess.TimeoutExpired:
        return _collect_failed(
            "TimeoutError", f"crawler exceeded {timeout}s wall clock"
        )
    except egress.EgressRefused as exc:
        # require-mode with no OS egress rung: report, don't crawl uncontained (§4.4/#19b).
        return _collect_failed("SandboxError", f"egress refused: {exc}")

    if returncode != 0:
        # A non-zero exit is the kernel killing the child on a breached rlimit
        # (RLIMIT_AS / RLIMIT_CPU) or an interpreter-level crash — report, don't raise.
        return _collect_failed(
            "SandboxError", f"child exited {returncode}", tail=stderr[-2000:]
        )
    try:
        return _parse_collect(result_text)
    except Exception as exc:
        # Honour the never-raise contract: a truncated/empty result file becomes a
        # failed CollectResult so one bad crawler can't abort the fleet (§4.4).
        return _collect_failed(
            "SandboxError",
            f"unparseable child result: {exc}",
            tail=(stderr + "\n--- result ---\n" + result_text)[-2000:],
        )


def _collect_failed(kind: str, message: str, *, tail: str = "") -> CollectResult:
    return CollectResult(
        items=[],
        errors=0,
        pages_fetched=0,
        exception=ExceptionInfo(type=kind, message=message, traceback_tail=tail),
    )


def _url_is_safe_to_store(url: str) -> bool:
    """A committed job's canonical url must be a real, non-internal http(s) target.

    Applied in the trusted parent — a belt on what a committed crawler *reports*,
    complementing the #19b egress allowlist that bounds where it can *connect*
    (:mod:`jobcrawler.runtime.egress`). Runs the §8 SSRF kernel with the url's *own*
    host allowed, so the on-domain arm never fires:
    this is a host-agnostic safety floor, not a board-domain check. It keeps a
    committed crawler that drifts against live data from seeding the tracking
    store (and the §4.9 recheck re-fetch) with a loopback / private / link-local /
    cloud-metadata / malformed url, while leaving any legitimate public link-host
    (e.g. a url_evidence sibling the generation gate admitted without a fetch
    grant) untouched. Board-domain validity is the generation gate's job (§6),
    which has the url_evidence the runtime lacks.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return False  # non-http(s) or hostless: the gate (§6) rejects these too
    try:
        check_url(url, {host})  # own host allowed → only the internal-target arm fires
    except SsrfError:
        return False
    return True


def _parse_collect(stdout: str) -> CollectResult:
    data = json.loads(stdout)
    items: list[Job] = []
    errors = int(data.get("errors", 0))
    for raw in data.get("items", []):
        try:
            job = Job.model_validate(raw)
        except Exception:  # a malformed job dump from the child (should not happen)
            errors += 1
            continue
        if not _url_is_safe_to_store(job.url):
            errors += 1  # an internal/malformed canonical url never enters the store
            continue
        items.append(job)
    exc = data.get("exception")
    exception = ExceptionInfo(**exc) if exc else None
    return CollectResult(
        items=items,
        errors=errors,
        pages_fetched=int(data.get("pages_fetched", 0)),
        exception=exception,
    )
