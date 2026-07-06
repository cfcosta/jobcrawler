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

from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.ctx import Ctx, crawler_facade
from jobcrawler.runtime.hardening import harden_xml
from jobcrawler.runtime.live_ctx import build_live_ctx
from jobcrawler.runtime.safe_exec import exec_crawler

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


def _build_ctx(
    allowed_hosts: set[str],
    replay: Mapping[str, Mapping[str, object]] | None,
    polite: bool = False,
) -> Ctx:
    if replay is None:
        # Live path. `polite` (set by `jobcrawl validate`) assembles the same §4.5
        # pacer+robots ctx the scheduled crawl uses, so a dry-run reflects production;
        # default-off keeps a bare ctx for offline generation/scoring.
        return build_live_ctx(allowed_hosts) if polite else Ctx(allowed_hosts)

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
    harden_xml()  # XXE / billion-laughs off before any candidate XML parse (§8)
    data = json.loads(sys.stdin.read())
    ctx = _build_ctx(
        set(data["allowed_hosts"]), data.get("replay"), bool(data.get("polite", False))
    )
    result: dict[str, object] = {
        "item_count": 0,
        "samples": [],
        "schema_errors": [],
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
    except Exception as exc:
        result["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback_tail": traceback.format_exc()[-2000:],
        }
    result["pages_fetched"] = ctx.pages_fetched
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
