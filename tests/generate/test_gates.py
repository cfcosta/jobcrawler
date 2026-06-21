"""Tests for the §6 hard acceptance gates (the binary adjudicators).

These are the fixed, host-owned pass/fail gates — never optimization targets
(§8). Gate 1 is the AST allowlist over ``crawler_source.code``; gate 2's hard
half is runtime completeness over a ``CandidateResult`` (the graded scalar is
``scoring.py``); gate 3 is two-run Jaccard stability over canonical URLs, which
hard-rejects only the near-random case and surfaces near-threshold as soft. The
must-observe predicate enforces "validate first / consult feeds before scrape".
"""

from __future__ import annotations

import inspect

from bizup_autocrawler.crawlers import example
from bizup_autocrawler.generate.gates import (
    StabilityVerdict,
    check_ast,
    check_completeness,
    jaccard_stability,
    must_observe_failure,
    stability_verdict,
)
from bizup_autocrawler.generate.scoring import CandidateResult, ExceptionInfo

_GOOD_SOURCE = """
from __future__ import annotations
import feedparser
from bizup_autocrawler.runtime.contract import Item

SITE = "https://example.com"

def crawl(ctx):
    feed = feedparser.parse(ctx.fetch(SITE + "/feed"))
    for e in feed.entries:
        yield Item(url=e.link, title=e.title, body=e.summary, source="example")
"""


# -- gate 1: AST allowlist ---------------------------------------------------


def test_clean_crawler_passes_ast() -> None:
    assert check_ast(_GOOD_SOURCE).passed


def test_reference_crawler_passes_ast() -> None:
    assert check_ast(inspect.getsource(example)).passed


def test_parsers_and_future_are_allowed() -> None:
    src = (
        "from __future__ import annotations\n"
        "from selectolax.parser import HTMLParser\n"
        "import lxml.etree\n"
        'SITE = "https://x.com"\n'
        "def crawl(ctx):\n    yield None\n"
    )
    assert check_ast(src).passed


def test_stdlib_xml_is_allowed_because_it_is_hardened() -> None:
    # stdlib xml.etree/xml.dom/xml.sax are permitted parsers — the §8 hardening
    # (runtime/hardening.py) defuses XXE/entity-expansion before any crawler runs.
    for imp in (
        "import xml.etree.ElementTree as ET",
        "from xml.dom import minidom",
        "import xml.sax",
    ):
        src = f'{imp}\nSITE = "https://x.com"\ndef crawl(ctx):\n    yield None\n'
        assert check_ast(src).passed, imp


def test_banned_import_os() -> None:
    res = check_ast('import os\nSITE="https://x.com"\ndef crawl(ctx):\n    yield 1\n')
    assert not res.passed
    assert any("os" in f for f in res.failures)


def test_banned_import_subprocess_and_socket() -> None:
    assert not check_ast(
        'import subprocess\nSITE="https://x.com"\ndef crawl(ctx):\n    yield 1\n'
    ).passed
    assert not check_ast(
        'from socket import socket\nSITE="https://x.com"\ndef crawl(ctx):\n    yield 1\n'
    ).passed


def test_banned_open_eval_exec_calls() -> None:
    for snippet in ("open('/etc/passwd')", "eval('1+1')", "exec('x=1')"):
        src = f'SITE="https://x.com"\ndef crawl(ctx):\n    {snippet}\n    yield 1\n'
        assert not check_ast(src).passed


def test_banned_dunder_attribute_escape() -> None:
    src = (
        'SITE="https://x.com"\n'
        "def crawl(ctx):\n"
        "    cls = ().__class__.__bases__\n"
        "    yield 1\n"
    )
    assert not check_ast(src).passed


def test_missing_site_or_crawl_fails() -> None:
    assert not check_ast("def crawl(ctx):\n    yield 1\n").passed
    assert not check_ast('SITE = "https://x.com"\n').passed


def test_crawl_must_accept_ctx() -> None:
    res = check_ast('SITE="https://x.com"\ndef crawl():\n    yield 1\n')
    assert not res.passed
    assert any("ctx" in f for f in res.failures)


def test_unparsable_source_is_truncation() -> None:
    res = check_ast('SITE = "https://x.com"\ndef crawl(ctx):\n    yield (')
    assert not res.passed
    assert any("parse" in f for f in res.failures)


# -- gate 2: runtime completeness (hard half) --------------------------------


def _good_sample(url: str = "https://example.com/a") -> dict[str, str]:
    return {"url": url, "title": "A real headline", "body": "x" * 600}


def _result(
    *,
    item_count: int,
    samples: list[dict[str, str]],
    schema_errors: list[str] | None = None,
    exception: ExceptionInfo | None = None,
) -> CandidateResult:
    return CandidateResult(
        item_count=item_count,
        samples=samples,
        schema_errors=schema_errors or [],
        pages_fetched=1,
        exception=exception,
    )


_HOSTS = {"example.com"}


def _complete(result: CandidateResult) -> bool:
    return check_completeness(
        result, min_items=5, min_body_chars=400, allowed_hosts=_HOSTS
    ).passed


def test_completeness_passes() -> None:
    assert _complete(_result(item_count=10, samples=[_good_sample()]))


def test_too_few_items_fails() -> None:
    assert not _complete(_result(item_count=2, samples=[_good_sample()]))


def test_exception_fails() -> None:
    exc = ExceptionInfo(type="ValueError", message="boom", traceback_tail="...")
    assert not _complete(_result(item_count=9, samples=[_good_sample()], exception=exc))


def test_schema_errors_fail() -> None:
    assert not _complete(
        _result(item_count=9, samples=[_good_sample()], schema_errors=["bad url"])
    )


def test_empty_title_sample_fails() -> None:
    s = _good_sample()
    s["title"] = "   "
    assert not _complete(_result(item_count=9, samples=[s]))


def test_short_body_sample_fails() -> None:
    s = _good_sample()
    s["body"] = "too short"
    assert not _complete(_result(item_count=9, samples=[s]))


def test_a_minority_of_short_bodies_still_passes() -> None:
    # 4 of 5 full, 1 short (a news brief): the runtime filter drops the short one,
    # so the gate must not reject the whole crawler over it (§6 aligns with §4.6).
    # Bodies are distinct so the boilerplate check doesn't fire on the filler.
    samples: list[dict[str, str]] = [
        {
            "url": f"https://example.com/a{i}",
            "title": "H",
            "body": f"Body {i}. " + "x" * 600,
        }
        for i in range(4)
    ]
    samples.append(
        {"url": "https://example.com/b", "title": "H", "body": "one-line brief"}
    )
    assert _complete(_result(item_count=9, samples=samples))


def test_mostly_short_bodies_fails() -> None:
    # 3 of 5 short → the crawler mostly yields incomplete bodies (e.g. RSS excerpts);
    # below the 0.6 quality floor, so reject it.
    full = [
        {
            "url": f"https://example.com/f{i}",
            "title": "H",
            "body": f"Full {i}. " + "x" * 600,
        }
        for i in range(2)
    ]
    shorts = [
        {"url": f"https://example.com/s{i}", "title": "H", "body": f"excerpt {i}"}
        for i in range(3)
    ]
    assert not _complete(_result(item_count=9, samples=full + shorts))


def test_mojibake_body_fails() -> None:
    s = _good_sample()
    s["body"] = "�" * 600
    assert not _complete(_result(item_count=9, samples=[s]))


def test_off_domain_sample_fails() -> None:
    assert not _complete(
        _result(item_count=9, samples=[_good_sample("https://evil.com/a")])
    )


def test_under_enumeration_fails_against_expected_count() -> None:
    # The site has ~40 recent articles; yielding 8 under-enumerates even though 8
    # clears the absolute floor — the crawler must consult the fuller source (§6).
    gate = check_completeness(
        _result(item_count=8, samples=[_good_sample()]),
        min_items=5,
        min_body_chars=400,
        allowed_hosts=_HOSTS,
        expected_items=40,
    )
    assert not gate.passed
    assert any("of ~40" in f for f in gate.failures)


def test_enumeration_floor_passes_when_close_enough() -> None:
    # 25 >= ceil(0.5 * 40) = 20 → enough of the recent set enumerated.
    assert check_completeness(
        _result(item_count=25, samples=[_good_sample()]),
        min_items=5,
        min_body_chars=400,
        allowed_hosts=_HOSTS,
        expected_items=40,
    ).passed


def test_no_expected_count_keeps_the_enumeration_floor_dormant() -> None:
    # Without a known expected count the relative floor cannot fire (only min_items).
    assert check_completeness(
        _result(item_count=8, samples=[_good_sample()]),
        min_items=5,
        min_body_chars=400,
        allowed_hosts=_HOSTS,
    ).passed


def test_shared_body_block_across_samples_fails() -> None:
    # Two different articles whose bodies share a trailing block = a related-links /
    # most-read rail scraped as body (the live Valor contamination). Reject it (§6).
    rail = "Leia também: " + "uma manchete de bloco relacionado repetida " * 3
    a = {
        "url": "https://example.com/a",
        "title": "A",
        "body": "Corpo A real.\n\n" + rail,
    }
    b = {
        "url": "https://example.com/b",
        "title": "B",
        "body": "Corpo B outro.\n\n" + rail,
    }
    gate = check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_body_chars=10,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("boilerplate" in f for f in gate.failures)


def test_distinct_bodies_pass_the_boilerplate_check() -> None:
    a = _good_sample("https://example.com/a")
    b = {"url": "https://example.com/b", "title": "B real headline", "body": "y" * 600}
    assert check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_body_chars=400,
        allowed_hosts=_HOSTS,
    ).passed


# -- gate 3: two-run Jaccard stability ---------------------------------------


def test_jaccard_identical_is_one() -> None:
    urls = ["https://example.com/a", "https://example.com/b"]
    assert jaccard_stability(urls, urls) == 1.0


def test_jaccard_ignores_tracking_params() -> None:
    a = ["https://example.com/a?utm_source=x"]
    b = ["https://example.com/a"]
    assert jaccard_stability(a, b) == 1.0  # canonicalization is load-bearing (§4.6)


def test_jaccard_disjoint_is_zero() -> None:
    assert jaccard_stability(["https://x.com/a"], ["https://x.com/b"]) == 0.0


def test_stability_verdict_pass_soft_reject() -> None:
    run = [f"https://x.com/{i}" for i in range(10)]
    assert stability_verdict(run, run, threshold=0.6)[0] is StabilityVerdict.PASS

    # 6 shared of 14 union → ~0.43: below the 0.6 threshold but not near-random → soft.
    partial = [f"https://x.com/{i}" for i in range(4, 14)]
    assert stability_verdict(run, partial, threshold=0.6)[0] is StabilityVerdict.SOFT

    disjoint = [f"https://x.com/z{i}" for i in range(10)]
    assert stability_verdict(run, disjoint, threshold=0.6)[0] is StabilityVerdict.REJECT


# -- gate 2(a): must-observe -------------------------------------------------


def test_must_observe_ok() -> None:
    assert (
        must_observe_failure(
            validated=True, discovery_method="feed", consulted_discovery=True
        )
        is None
    )


def test_must_observe_requires_validation() -> None:
    assert (
        must_observe_failure(
            validated=False, discovery_method="feed", consulted_discovery=True
        )
        is not None
    )


def test_must_observe_scrape_requires_consulting_feeds() -> None:
    assert (
        must_observe_failure(
            validated=True, discovery_method="scrape", consulted_discovery=False
        )
        is not None
    )
    assert (
        must_observe_failure(
            validated=True, discovery_method="scrape", consulted_discovery=True
        )
        is None
    )
