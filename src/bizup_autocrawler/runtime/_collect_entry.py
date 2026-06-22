"""Subprocess entry for §4.4 isolation of a *committed* crawl — never in-process.

The sibling of ``_sandbox_entry``: same isolated exec of an (already AST-gated)
crawler through a ctx-only network path, but it emits **every** item in full
(``url/title/body/published_at/source``) rather than 5 size-bounded samples, so
the host runner can dedup + ingest them. A committed crawl runs against the *live*
site (no replay) — SSRF-guarded as ever. Imports stay light (no dspy) so the child
starts fast under the memory rlimit.

Reads ``{source, allowed_hosts}`` as JSON on stdin; writes
``{items, errors, pages_fetched, exception}`` as JSON to stdout.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable, Iterable
from typing import cast

from bizup_autocrawler.runtime.contract import Item
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.hardening import harden_xml


def _build_ctx(allowed_hosts: set[str]) -> Ctx:
    # A live, SSRF-guarded ctx — the committed crawl fetches the real site. This is
    # the seam the production live-Ctx factory enriches with the §4.5 per-host pacer,
    # robots policy, and the §4.10 solver/clearance + egress proxy from config.
    return Ctx(allowed_hosts)


def main() -> None:
    harden_xml()  # XXE / billion-laughs off before any candidate XML parse (§8)
    data = json.loads(sys.stdin.read())
    ctx = _build_ctx(set(data["allowed_hosts"]))
    items: list[dict[str, object]] = []
    errors = 0
    exception: dict[str, str] | None = None
    try:
        namespace: dict[str, object] = {}
        exec(compile(data["source"], "<crawler>", "exec"), namespace)  # noqa: S102
        crawl = cast("Callable[[Ctx], Iterable[object]]", namespace["crawl"])
        for obj in crawl(ctx):
            item = obj if isinstance(obj, Item) else None
            if item is None:
                try:
                    item = Item.model_validate(obj)
                except Exception:  # contract violation, not a crash
                    errors += 1
                    continue
            items.append(item.model_dump(mode="json"))
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
