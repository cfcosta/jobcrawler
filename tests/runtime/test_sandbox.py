"""Tests for the §4.4 subprocess sandbox (validate_crawler).

These spawn the real child process and exercise the full path: AST gate →
isolated exec → ctx-only network (served from a replay map, so no live traffic)
→ size-bounded CandidateResult. They pin that a good candidate yields items, the
gate blocks banned code before running, runtime exceptions and the SSRF guard
are captured (not crashes), and the wall-clock timeout hard-stops a hang.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobcrawler.runtime.sandbox import (
    _child_env,
    _parse_collect,
    _url_is_safe_to_store,
    collect_crawler,
    validate_crawler,
)

_GOOD = """
from jobcrawler.runtime.contract import Job

SITE = "https://example.com"

def crawl(ctx):
    ctx.fetch(SITE + "/feed")
    for i in range(3):
        yield Job(url=f"{SITE}/a{i}", title=f"T{i}", company="Acme", location="Remote",
                  description="x" * 500, source="example")
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
    # Sample descriptions cross the child→parent pipe FAITHFULLY — they are the §6
    # evidence the gate and description axis grade, and clipping them here once
    # collapsed boards that stamp a long shared intro on every role into
    # "identical descriptions" rejections of honest crawlers. Only a pathological
    # description hits the pipe bound, and the sample *count* stays capped.
    big = """
from jobcrawler.runtime.contract import Job
SITE = "https://example.com"
def crawl(ctx):
    for i in range(50):
        desc = "y" * 5000 if i % 2 == 0 else "z" * 30000
        yield Job(url=f"{SITE}/{i}", title="T", company="Acme", location="Remote",
                  description=desc, source="s")
"""
    result = validate_crawler(big, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.item_count == 50
    assert len(result.samples) == 5  # capped
    assert len(result.samples[0]["description"]) == 5000  # faithful, not previewed
    assert len(result.samples[1]["description"]) == 20_000  # pipe-bounded


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
from jobcrawler.runtime.contract import Job
SITE = "https://example.com"
def crawl(ctx):
    doc = ctx.fetch(SITE + "/feed")
    root = ET.fromstring(doc)
    for item in root.findall("channel/item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        yield Job(url=link, title=title, company="Acme", location="Remote",
                  description="x" * 500, source="example")
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
    from jobcrawler.runtime._sandbox_entry import _build_ctx

    ctx = _build_ctx({"example.com"}, None, polite=False)
    assert ctx._pacer is None
    assert ctx._robots is None


def test_build_ctx_live_polite_mirrors_the_production_crawl() -> None:
    # `jobcrawl validate` opts in (polite=True), so its live ctx is assembled by the
    # SAME build_live_ctx factory the scheduled crawl uses — per-host pacer + robots on,
    # so a dry-run reflects exactly what production will (and won't) fetch.
    from jobcrawler.runtime._sandbox_entry import _build_ctx

    ctx = _build_ctx({"example.com"}, None, polite=True)
    assert ctx._pacer is not None
    assert ctx._robots is not None


# -- child env scrub (§8, #19a) ----------------------------------------------


def test_child_env_forwards_crawl_knobs_and_drops_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The crawl knobs the child's live-ctx path legitimately reads ride through...
    monkeypatch.setenv("JOBCRAWLER_USER_AGENT", "ua/1")
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "5/s")
    monkeypatch.setenv("JOBCRAWLER_EGRESS_PROXY", "http://proxy:8080")
    monkeypatch.setenv("FLARESOLVERR_URL", "http://fs:8191")
    # ...but no provider key, model slug, or OAuth-dir pointer ever does: the
    # untrusted child never talks to an LLM, so these are pure loot to withhold.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret2")
    monkeypatch.setenv("JOBCRAWLER_MODEL", "openrouter/vendor/model")
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", "/run/secrets/chatgpt")
    monkeypatch.setenv("CHATGPT_TOKEN_DIR", "/run/secrets/codex")

    env = _child_env()

    assert env["JOBCRAWLER_USER_AGENT"] == "ua/1"
    assert env["JOBCRAWLER_RATE_LIMIT"] == "5/s"
    assert env["JOBCRAWLER_EGRESS_PROXY"] == "http://proxy:8080"
    assert env["FLARESOLVERR_URL"] == "http://fs:8191"
    for secret in (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "JOBCRAWLER_MODEL",
        "JOBCRAWLER_AUTH_DIR",
        "CHATGPT_TOKEN_DIR",
    ):
        assert secret not in env


def test_child_env_injects_an_importable_package_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The scrub drops whatever ambient var put the src layout on the path, so the
    # child is handed an explicit PYTHONPATH naming the dir that CONTAINS the
    # package — without it the scrubbed child can't import jobcrawler at all.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    import jobcrawler

    pkg_parent = str(Path(jobcrawler.__file__).resolve().parents[1])
    env = _child_env()

    assert env["PYTHONPATH"] == pkg_parent
    assert (Path(env["PYTHONPATH"]) / "jobcrawler" / "__init__.py").exists()


def test_child_env_prepends_the_package_path_to_an_existing_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A deployment that already sets PYTHONPATH keeps it — the package dir is
    # prepended (wins on collision) rather than clobbering the operator's value.
    monkeypatch.setenv("PYTHONPATH", "/custom/deps")
    env = _child_env()

    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == "/custom/deps"
    assert (Path(parts[0]) / "jobcrawler" / "__init__.py").exists()


# -- result channel: off the crawler-shared stdout (§8, #22) -----------------


_NOISY = """
from jobcrawler.runtime.contract import Job

SITE = "https://example.com"

def crawl(ctx):
    print("noise on stdout")           # a bare debug print...
    ctx.log("chatty crawler")          # ...and a ctx.log both hit the child stdout
    print('{"item_count": 9999, "samples": [], "pages_fetched": 0, "exception": null}')
    ctx.fetch(SITE + "/feed")
    yield Job(url=SITE + "/a", title="T", company="Acme", location="Remote",
              description="x" * 500, source="example")
"""


def test_crawler_stdout_noise_neither_corrupts_nor_spoofs_the_result() -> None:
    # The result now rides a private file, not stdout, so a crawler is free to
    # print — its noise can't corrupt the JSON the parent parses, and the forged
    # `item_count: 9999` line above is ignored in favour of the one real yield.
    # Before #22 this candidate's stdout collapsed validation to a SandboxError.
    result = validate_crawler(
        _NOISY, allowed_hosts={"example.com"}, replay=_REPLAY, timeout=20.0
    )
    assert result.exception is None
    assert result.item_count == 1


def test_collect_result_survives_crawler_stdout_noise() -> None:
    # Same decoupling on the committed-crawl path. This crawler yields without
    # fetching, so it needs no network — the point is only that its stdout print
    # doesn't reach the result channel.
    src = (
        "from jobcrawler.runtime.contract import Job\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        '    print("noise")\n'
        "    yield Job(url=SITE + '/a', title='T', company='Acme', location='Remote',\n"
        "              description='x' * 500, source='example')\n"
    )
    result = collect_crawler(src, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is None
    assert len(result.items) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud-metadata (link-local)
        "http://127.0.0.1/admin",  # loopback
        "http://10.0.0.5/internal",  # private
        "http://metadata.google.internal/",  # blocked metadata hostname
        "not a url",  # malformed — no host
        "ftp://example.com/x",  # a host, but check_url only guards http(s) hosts
    ],
)
def test_url_is_not_safe_to_store(url: str) -> None:
    # The committed-collect safety floor: an internal / malformed canonical url must
    # never reach the tracking store (and thus the §4.9 recheck re-fetch), even
    # though the Job schema (min_length=1) would happily accept it.
    assert not _url_is_safe_to_store(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/jobs/1",  # the crawler's own fetch host
        "https://jobs.ashbyhq.com/acme/1",  # a public sibling link-host, off the
        "https://careers.acme.io/1",  # fetch allowlist — kept (recheck SSRF-guards
    ],  # the fetch; board-domain validity is the generation gate's job, §6)
)
def test_url_is_safe_to_store(url: str) -> None:
    # Host-agnostic: any real, external http(s) url passes — the floor drops only
    # internal/malformed targets, never a legitimate public link-host the gate
    # admitted via url_evidence without a fetch grant.
    assert _url_is_safe_to_store(url)


def test_parse_collect_drops_internal_url_and_counts_it() -> None:
    # Two well-formed Jobs from the child; the one pointing at the metadata IP is
    # dropped by the trusted parent and folded into ``errors`` — the store only ever
    # sees the safe one, independent of whether the (in-process) child filtered.
    stdout = json.dumps(
        {
            "items": [
                {
                    "url": "https://example.com/a",
                    "title": "T",
                    "company": "Acme",
                    "location": "Remote",
                    "description": "x" * 500,
                    "source": "example",
                },
                {
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "title": "T2",
                    "company": "Acme",
                    "location": "Remote",
                    "description": "y" * 500,
                    "source": "example",
                },
            ],
            "errors": 0,
            "pages_fetched": 1,
            "exception": None,
        }
    )
    result = _parse_collect(stdout)
    assert [job.url for job in result.items] == ["https://example.com/a"]
    assert result.errors == 1


def test_collect_drops_internal_url_end_to_end() -> None:
    # Full path: a committed crawler that yields one on-domain job and one pointing
    # at the cloud-metadata endpoint. The parent keeps the safe job and drops the
    # internal one — a drifted committed crawler can't seed the store with an SSRF
    # target. Yields without fetching, so it needs no live network.
    src = (
        "from jobcrawler.runtime.contract import Job\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        "    yield Job(url=SITE + '/a', title='T', company='Acme', location='Remote',\n"
        "              description='x' * 500, source='example')\n"
        "    yield Job(url='http://169.254.169.254/x', title='T2', company='Acme',\n"
        "              location='Remote', description='y' * 500, source='example')\n"
    )
    result = collect_crawler(src, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is None
    assert [job.url for job in result.items] == ["https://example.com/a"]
    assert result.errors == 1
