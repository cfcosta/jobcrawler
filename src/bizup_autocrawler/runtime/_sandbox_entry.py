"""Subprocess entry point for the §4.4 sandbox — never imported in-process.

Reads ``{source, allowed_hosts, replay}`` as JSON on stdin, execs the (already
AST-gated) candidate, runs ``crawl(ctx)`` collecting items, and writes a
size-bounded CandidateResult dict as JSON to stdout. The ``ctx`` is the only
network path; with ``replay`` it serves recorded HTTP (offline metric), else it
makes real, SSRF-guarded requests. Imports stay light (no dspy) so the child
starts fast under the memory rlimit.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping
from typing import cast

import httpx

from bizup_autocrawler.runtime.contract import Item
from bizup_autocrawler.runtime.ctx import Ctx

_SAMPLE_CAP = 5
_BODY_PREVIEW = 600
_ERROR_CAP = 10


def _build_ctx(
    allowed_hosts: set[str], replay: Mapping[str, Mapping[str, object]] | None
) -> Ctx:
    if replay is None:
        return Ctx(allowed_hosts)

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
    data = json.loads(sys.stdin.read())
    ctx = _build_ctx(set(data["allowed_hosts"]), data.get("replay"))
    result: dict[str, object] = {
        "item_count": 0,
        "samples": [],
        "schema_errors": [],
        "pages_fetched": 0,
        "exception": None,
    }
    try:
        namespace: dict[str, object] = {}
        exec(compile(data["source"], "<candidate>", "exec"), namespace)  # noqa: S102
        crawl = cast("Callable[[Ctx], Iterable[object]]", namespace["crawl"])
        count = 0
        samples: list[dict[str, str]] = []
        errors: list[str] = []
        for obj in crawl(ctx):
            item = obj if isinstance(obj, Item) else None
            if item is None:
                try:
                    item = Item.model_validate(obj)
                except Exception as exc:  # contract violation, not a crash
                    if len(errors) < _ERROR_CAP:
                        errors.append(str(exc)[:200])
                    continue
            count += 1
            if len(samples) < _SAMPLE_CAP:
                samples.append(
                    {
                        "url": item.url,
                        "title": item.title,
                        "body": item.body[:_BODY_PREVIEW],
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
