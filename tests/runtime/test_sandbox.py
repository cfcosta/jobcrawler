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


def test_wall_clock_timeout_hard_stops_a_hang() -> None:
    hang = "SITE='https://x.com'\ndef crawl(ctx):\n    while True:\n        pass\n    yield None\n"
    result = validate_crawler(hang, allowed_hosts={"x.com"}, timeout=1.0)
    assert result.exception is not None
    assert result.exception.type == "TimeoutError"
