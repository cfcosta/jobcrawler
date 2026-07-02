"""Subprocess entry for §4.4 isolation of a *committed* crawl — never in-process.

The sibling of ``_sandbox_entry``: same isolated exec of an (already AST-gated)
crawler through a ctx-only network path, but it emits **every** job in full (the
whole §4.6 ``Job``: url/title/company/location/description/source + optional
salary/workplace/timestamps) rather than 5 size-bounded samples, so the host
runner can dedup + track them. A committed crawl runs against the *live* site (no
replay) — SSRF-guarded as ever. Imports stay light (no dspy) so the child starts
fast under the memory rlimit.

Reads ``{source, allowed_hosts}`` as JSON on stdin; writes
``{items, errors, pages_fetched, exception}`` as JSON to stdout.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable, Iterable
from typing import cast

from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.hardening import harden_xml
from jobcrawler.runtime.live_ctx import build_live_ctx
from jobcrawler.runtime.safe_exec import exec_crawler


def _build_ctx(allowed_hosts: set[str]) -> Ctx:
    # The production crawl path: a live, SSRF-guarded ctx that fetches the real site
    # through the §4.5 per-host pacer + robots policy and the §4.10 solver/clearance +
    # egress proxy, all assembled from config. Config is read here (in the child),
    # which inherits the daemon's environment. Politeness is on by default — a
    # committed crawler never contains any of this logic.
    return build_live_ctx(allowed_hosts)


def main() -> None:
    harden_xml()  # XXE / billion-laughs off before any candidate XML parse (§8)
    data = json.loads(sys.stdin.read())
    ctx = _build_ctx(set(data["allowed_hosts"]))
    items: list[dict[str, object]] = []
    errors = 0
    exception: dict[str, str] | None = None
    try:
        # Exec under the §8 restricted ``__builtins__`` (eval/exec/open/raw
        # __import__ absent; imports allowlisted) — the runtime half of the sandbox,
        # not trusting the AST gate alone.
        namespace = exec_crawler(data["source"], filename="<crawler>")
        crawl = cast("Callable[[Ctx], Iterable[object]]", namespace["crawl"])
        for obj in crawl(ctx):
            job = obj if isinstance(obj, Job) else None
            if job is None:
                try:
                    job = Job.model_validate(obj)
                except Exception:  # contract violation, not a crash
                    errors += 1
                    continue
            items.append(job.model_dump(mode="json"))
    except Exception as exc:  # a mid-crawl raise: keep partial items, record the crash
        exception = {
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback_tail": traceback.format_exc()[-2000:],
        }
    sys.stdout.write(
        json.dumps(
            {
                "items": items,
                "errors": errors,
                "pages_fetched": ctx.pages_fetched,
                "exception": exception,
            }
        )
    )


if __name__ == "__main__":
    main()
