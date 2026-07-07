"""Subprocess entry point for the §4.4 sandbox — never imported in-process.

Reads ``{source, allowed_hosts, replay}`` as JSON on stdin, execs the (already
AST-gated) candidate, runs ``crawl(ctx)`` collecting the :class:`Job` postings it
yields, and writes a size-bounded CandidateResult dict as JSON to stdout. The
``ctx`` is the only network path; with ``replay`` it serves recorded HTTP (offline
metric), else it makes real, SSRF-guarded requests — per-host paced +
robots-compliant when ``polite`` (the ``jobcrawl validate`` preview), bare
otherwise. Imports stay light (no dspy) so the child starts fast under the memory
rlimit.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping
from typing import cast

import httpx

from jobcrawler import config
from jobcrawler.runtime import egress
from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.ctx import Ctx, crawler_facade
from jobcrawler.runtime.hardening import harden_xml
from jobcrawler.runtime.live_ctx import build_live_ctx
from jobcrawler.runtime.safe_exec import exec_crawler
from jobcrawler.urls import normalize_host, valid_on_domain_url

_SAMPLE_CAP = 5
# Samples are §6 *evidence*: the completeness gate and the §4.11 description axis
# grade these exact strings, so they must be faithful to what the crawler yielded.
# The cap only bounds the child→parent JSON pipe against a pathological candidate
# (a multi-megabyte "description") and sits far above real posting text — boards
# that stamp a shared intro on every role (GitLab's runs ~1.4k chars) must not
# collapse to identical samples under the clip, or the identity gate rejects
# honest crawlers. The model-facing preview clip lives in the `validate_crawler`
# *tool* (generate/tools.py), which owns the RLM bridge budget (§4.3).
_DESCRIPTION_CAP = 20_000
_ERROR_CAP = 10
# Distinct off-domain hosts reported to the gate. A handful is all the gate needs
# to reject; the cap only bounds the child→parent pipe against a pathological
# candidate that yields thousands of distinct off-domain hosts.
_OFFDOMAIN_HOST_CAP = 32


def _build_ctx(
    allowed_hosts: set[str],
    replay: Mapping[str, Mapping[str, object]] | None,
    polite: bool = False,
) -> Ctx:
    if replay is None:
        # Live path. `polite` (set by `jobcrawl validate`) assembles the same §4.5
        # pacer+robots ctx the scheduled crawl uses, so a dry-run reflects production;
        # default-off keeps a bare ctx for offline generation/scoring. Both route
        # through the #19b loopback allowlist proxy: `build_live_ctx` already honors
        # `egress_proxy`, and the bare Ctx gets it threaded explicitly here (the parent
        # overrides `JOBCRAWLER_EGRESS_PROXY` to the loopback chokepoint).
        if polite:
            return build_live_ctx(allowed_hosts)
        return Ctx(allowed_hosts, proxy=config.egress_proxy())

    def handler(request: httpx.Request) -> httpx.Response:
        record = replay.get(str(request.url))
        if record is None:
            return httpx.Response(404)
        status = record.get("status", 200)
        return httpx.Response(
            status if isinstance(status, int) else 200,
            text=str(record.get("text", "")),
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return Ctx(allowed_hosts, client=client)


def main() -> None:
    egress.arm_child_if_selected()  # arm the OS egress rung the parent selected (#19b)
    harden_xml()  # XXE / billion-laughs off before any candidate XML parse (§8)
    data = json.loads(sys.stdin.read())
    allowed_hosts = set(data["allowed_hosts"])
    ctx = _build_ctx(
        allowed_hosts, data.get("replay"), bool(data.get("polite", False))
    )
    result: dict[str, object] = {
        "item_count": 0,
        "samples": [],
        "schema_errors": [],
        "offdomain_hosts": [],
        "pages_fetched": 0,
        "exception": None,
    }
    try:
        # Exec under the §8 restricted ``__builtins__`` (eval/exec/open/raw
        # __import__ absent; imports allowlisted) — the runtime half of the sandbox,
        # not trusting the AST gate alone.
        namespace = exec_crawler(data["source"], filename="<candidate>")
        crawl = cast("Callable[[object], Iterable[object]]", namespace["crawl"])
        count = 0
        samples: list[dict[str, str]] = []
        errors: list[str] = []
        # Distinct hosts a yielded url pointed at that are off the fetch allowlist,
        # over the WHOLE yield (not just the first _SAMPLE_CAP samples). The §6
        # completeness gate rejects on this so a crawler that is clean for five then
        # emits off-domain urls in the tail can't slip past the sample check. The
        # gate re-filters against its own (url_evidence-widened) allowed set, so a
        # legitimate sibling link-host is not double-punished here.
        offdomain: dict[str, None] = {}
        # Hand the crawler the capability facade, never the enforcing ctx (§8):
        # the real ctx stays here so pages_fetched below reads the true budget.
        for obj in crawl(crawler_facade(ctx)):
            job = obj if isinstance(obj, Job) else None
            if job is None:
                try:
                    job = Job.model_validate(obj)
                except Exception as exc:  # contract violation, not a crash
                    if len(errors) < _ERROR_CAP:
                        errors.append(str(exc)[:200])
                    continue
            count += 1
            if not valid_on_domain_url(job.url, allowed_hosts):
                host = normalize_host(job.url) or "(no-host)"
                if host not in offdomain and len(offdomain) < _OFFDOMAIN_HOST_CAP:
                    offdomain[host] = None
            if len(samples) < _SAMPLE_CAP:
                samples.append(
                    {
                        "url": job.url,
                        "title": job.title,
                        # company/location are required Job fields the §6 completeness
                        # gate checks per sample — omitting them here would fail every
                        # candidate at the gate.
                        "company": job.company,
                        "location": job.location,
                        "description": job.description[:_DESCRIPTION_CAP],
                    }
                )
        result["item_count"] = count
        result["samples"] = samples
        result["schema_errors"] = errors
        result["offdomain_hosts"] = list(offdomain)
    except Exception as exc:
        result["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback_tail": traceback.format_exc()[-2000:],
        }
    result["pages_fetched"] = ctx.pages_fetched
    _emit_result(result)


def _emit_result(result: dict[str, object]) -> None:
    # The result goes to the private file the parent named on argv (argv[1]), never
    # stdout: stdout is shared with the untrusted crawler (a bare print, ctx.log),
    # so emitting the JSON there would let a posting's logging corrupt it or a
    # hostile candidate forge a CandidateResult (§8). See runtime.sandbox._run_child.
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        fh.write(json.dumps(result))


if __name__ == "__main__":
    main()
