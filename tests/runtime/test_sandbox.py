"""Tests for the §4.4 subprocess sandbox (validate_crawler).

These spawn the real child process and exercise the full path: AST gate →
isolated exec → ctx-only network (served from a replay map, so no live traffic)
→ size-bounded CandidateResult. They pin that a good candidate yields items, the
gate blocks banned code before running, runtime exceptions and the SSRF guard
are captured (not crashes), and the wall-clock timeout hard-stops a hang.
"""

from __future__ import annotations

from bizup_autocrawler.runtime.sandbox import validate_crawler

_GOOD = """
from bizup_autocrawler.runtime.contract import Item

SITE = "https://example.com"

def crawl(ctx):
    ctx.fetch(SITE + "/feed")
    for i in range(3):
        yield Item(url=f"{SITE}/a{i}", title=f"T{i}", body="x" * 500, source="example")
"""

_REPLAY = {"https://example.com/feed": {"status": 200, "text": "<feed/>"}}


def test_good_candidate_yields_items() -> None:
    result = validate_crawler(
        _GOOD, allowed_hosts={"example.com"}, replay=_REPLAY, timeout=20.0
    )
    assert result.exception is None
    assert result.item_count == 3
    assert len(result.samples) == 3
    assert result.samples[0]["title"] == "T0"
    assert result.pages_fetched == 1  # the one ctx.fetch


def test_samples_and_bodies_are_size_bounded() -> None:
    big = """
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    for i in range(50):
        yield Item(url=f"{SITE}/{i}", title="T", body="y" * 5000, source="s")
"""
    result = validate_crawler(big, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.item_count == 50
    assert len(result.samples) == 5  # capped
    assert len(result.samples[0]["body"]) == 600  # truncated


def test_ast_gate_blocks_banned_code_before_running() -> None:
    banned = "import os\nSITE='https://x.com'\ndef crawl(ctx):\n    yield 1\n"
    result = validate_crawler(banned, allowed_hosts={"x.com"}, timeout=20.0)
    assert result.exception is not None
    assert result.exception.type == "ASTGateError"


def test_sandbox_blocks_builtins_escape() -> None:
    # A crawler trying to climb out via __builtins__ never reaches exec: the AST gate
    # rejects the __builtins__ name first. Even past it, the child execs under a
    # restricted __builtins__ (no __import__/eval) — so the escape is reported, not run.
    escape = (
        'SITE="https://example.com"\n'
        "def crawl(ctx):\n"
        '    __builtins__["__import__"]("os").system("id")\n'
        "    yield None\n"
    )
    result = validate_crawler(escape, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is not None
    assert result.exception.type == "ASTGateError"


def test_runtime_exception_is_captured() -> None:
    raises = """
SITE = "https://example.com"
def crawl(ctx):
    raise RuntimeError("boom in crawl")
    yield None
"""
    result = validate_crawler(raises, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is not None
    assert result.exception.type == "RuntimeError"
    assert "boom in crawl" in result.exception.message


def test_ssrf_guard_blocks_internal_target_in_sandbox() -> None:
    ssrf = """
SITE = "https://example.com"
def crawl(ctx):
    ctx.fetch("http://169.254.169.254/latest/meta-data")
    yield None
"""
    result = validate_crawler(ssrf, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is not None
    assert result.exception.type == "SsrfError"


def test_sandbox_hardens_xxe_in_generated_xml_parsing() -> None:
    # A crawler that parses an attacker-controlled feed with a SYSTEM entity. The
    # sandbox entry hardens XML (§8) before exec, so defusedxml refuses the entity
    # rather than reading file:///etc/passwd — captured as an exception, not a hang.
    xxe = """
import xml.etree.ElementTree as ET
SITE = "https://example.com"
def crawl(ctx):
    doc = ctx.fetch(SITE + "/feed")
    root = ET.fromstring(doc)
    yield None
"""
    replay = {
        "https://example.com/feed": {
            "status": 200,
            "text": (
                '<?xml version="1.0"?>'
                '<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                "<r>&xxe;</r>"
            ),
        }
    }
    result = validate_crawler(
        xxe, allowed_hosts={"example.com"}, replay=replay, timeout=20.0
    )
    assert result.exception is not None
    assert result.exception.type == "EntitiesForbidden"


def test_sandbox_allows_benign_stdlib_xml_parsing() -> None:
    # The flip side: stdlib xml is a *permitted* parser now that it is defused, so
    # a well-formed feed parses and yields normally.
    good = """
import xml.etree.ElementTree as ET
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    doc = ctx.fetch(SITE + "/feed")
    root = ET.fromstring(doc)
    for item in root.findall("channel/item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        yield Item(url=link, title=title, body="x" * 500, source="example")
"""
    replay = {
        "https://example.com/feed": {
            "status": 200,
            "text": (
                "<rss><channel>"
                "<item><title>Alpha</title><link>https://example.com/a</link></item>"
                "<item><title>Beta</title><link>https://example.com/b</link></item>"
                "</channel></rss>"
            ),
        }
    }
    result = validate_crawler(
        good, allowed_hosts={"example.com"}, replay=replay, timeout=20.0
    )
    assert result.exception is None
    assert result.item_count == 2
    assert {s["title"] for s in result.samples} == {"Alpha", "Beta"}


def test_wall_clock_timeout_hard_stops_a_hang() -> None:
    hang = "SITE='https://x.com'\ndef crawl(ctx):\n    while True:\n        pass\n    yield None\n"
    result = validate_crawler(hang, allowed_hosts={"x.com"}, timeout=1.0)
    assert result.exception is not None
    assert result.exception.type == "TimeoutError"


def test_build_ctx_live_is_bare_by_default() -> None:
    # Offline generation/scoring keeps a bare live Ctx (no pacer/robots): its restraint
    # comes from the replay map and the page budget, not §4.5 throttling — so the RLM's
    # exploration is not silently reshaped by robots/pacing.
    from bizup_autocrawler.runtime._sandbox_entry import _build_ctx

    ctx = _build_ctx({"example.com"}, None, polite=False)
    assert ctx._pacer is None
    assert ctx._robots is None


def test_build_ctx_live_polite_mirrors_the_production_crawl() -> None:
    # `autocrawl validate` opts in (polite=True), so its live ctx is assembled by the
    # SAME build_live_ctx factory the scheduled crawl uses — per-host pacer + robots on,
    # so a dry-run reflects exactly what production will (and won't) fetch.
    from bizup_autocrawler.runtime._sandbox_entry import _build_ctx

    ctx = _build_ctx({"example.com"}, None, polite=True)
    assert ctx._pacer is not None
    assert ctx._robots is not None
