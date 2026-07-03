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

import pytest

from jobcrawler.generate.gates import (
    StabilityVerdict,
    admissible_hosts,
    check_ast,
    check_completeness,
    check_host_admission,
    defective_description_indices,
    jaccard_stability,
    must_observe_failure,
    stability_verdict,
)
from jobcrawler.generate.scoring import CandidateResult, ExceptionInfo

_GOOD_SOURCE = """
from __future__ import annotations
import feedparser
from jobcrawler.runtime.contract import Job

SITE = "https://example.com"

def crawl(ctx):
    feed = feedparser.parse(ctx.fetch(SITE + "/feed"))
    for e in feed.entries:
        yield Job(
            url=e.link,
            title=e.title,
            company="Example Co",
            location="Remote",
            description=e.summary,
            source="example",
        )
"""


# -- gate 1: AST allowlist ---------------------------------------------------


def test_clean_crawler_passes_ast() -> None:
    assert check_ast(_GOOD_SOURCE).passed


def test_reference_crawler_passes_ast() -> None:
    # The committed reference crawler (crawlers/example.py) is a sibling module,
    # migrated to the §4.6 Job contract out of band; skip if it isn't importable in
    # this environment yet (pytest.importorskip re-raises nested ImportErrors, so
    # catch it explicitly). Once present it must clear gate 1 like any crawler.
    try:
        from jobcrawler.crawlers import example
    except ImportError as exc:  # sibling not yet Job-migrated
        pytest.skip(f"reference crawler not importable yet: {exc}")
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


def test_banned_builtins_name_escape() -> None:
    # The classic gate bypass: reach the builtins mapping by name, then index a raw
    # __import__/eval out of it. Banning __builtins__ (any dunder name) shuts it (§8).
    src = (
        'SITE="https://x.com"\n'
        "def crawl(ctx):\n"
        '    imp = __builtins__["__import__"]\n'
        "    yield imp\n"
    )
    res = check_ast(src)
    assert not res.passed
    assert any("__builtins__" in f for f in res.failures)


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


# -- gate 1(b): EXTRA_HOSTS shape + host admission (§8) -------------------------


def _declaring(extra: str) -> str:
    return (
        'SITE = "https://example.com"\n'
        f"EXTRA_HOSTS = {extra}\n"
        "def crawl(ctx):\n    yield 1\n"
    )


def test_extra_hosts_literal_tuple_passes_ast() -> None:
    assert check_ast(_declaring('("api.example.net",)')).passed


def test_extra_hosts_computed_shape_fails_ast() -> None:
    res = check_ast(_declaring("tuple(h for h in [])"))
    assert not res.passed
    assert any("literal tuple/list" in f for f in res.failures)


def test_extra_hosts_non_string_entry_fails_ast() -> None:
    res = check_ast(_declaring('("api.example.net", 3)'))
    assert not res.passed
    assert any("string literals" in f for f in res.failures)


def test_extra_hosts_url_shaped_entry_fails_ast() -> None:
    # A scheme/path/port could smuggle more than a host into the allowlist
    # derivation — only bare hostnames are declarable.
    res = check_ast(_declaring('("https://api.example.net/path",)'))
    assert not res.passed
    assert any("bare hostname" in f for f in res.failures)


def test_admission_passes_an_evidence_backed_declaration() -> None:
    res = check_host_admission(
        _declaring('("api.example.net",)'),
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net"],
        cap=3,
    )
    assert res.passed, res.reason


def test_admission_accepts_a_subdomain_of_an_evidence_host() -> None:
    res = check_host_admission(
        _declaring('("eu.api.example.net",)'),
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net"],
        cap=3,
    )
    assert res.passed, res.reason


def test_admission_without_declarations_needs_no_evidence() -> None:
    src = 'SITE = "https://example.com"\ndef crawl(ctx):\n    yield 1\n'
    res = check_host_admission(
        src, registry_hosts={"example.com"}, evidence_hosts=[], cap=3
    )
    assert res.passed, res.reason


def test_admission_rejects_a_declaration_without_evidence() -> None:
    # Declaration alone authorizes nothing (§8): the generation context is
    # attacker-influenced (job pages carry arbitrary text), so only hosts the
    # site's own served bytes vouch for are admissible.
    res = check_host_admission(
        _declaring('("api.example.net",)'),
        registry_hosts={"example.com"},
        evidence_hosts=[],
        cap=3,
    )
    assert not res.passed
    assert any("not observed" in f for f in res.failures)


def test_admissible_hosts_admits_only_evidence_backed_declarations() -> None:
    # The allowlist generation-time validation and the reward run under: the
    # registry plus declared ∩ evidence. An unevidenced declaration stays out
    # (declaration alone authorizes nothing), and so does undeclared evidence —
    # the committed crawler won't have it either.
    hosts = admissible_hosts(
        _declaring('("api.example.net", "evil.attacker.io")'),
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net", "cdn.example.net"],
    )
    assert hosts == {"example.com", "api.example.net"}


def test_admissible_hosts_uses_the_gate_domain_rule() -> None:
    # Same host_on_domain evidence match as check_host_admission: a declared
    # subdomain of an evidence host is admitted, keeping the two derivations
    # aligned so validation can't reject what admission would grant.
    hosts = admissible_hosts(
        _declaring('("eu.api.example.net",)'),
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net"],
    )
    assert "eu.api.example.net" in hosts


def test_admissible_hosts_on_unparseable_source_keeps_registry() -> None:
    hosts = admissible_hosts(
        "def broken(:",
        registry_hosts={"example.com"},
        evidence_hosts=["api.example.net"],
    )
    assert hosts == {"example.com"}


def test_admission_rejects_over_cap() -> None:
    hosts = ["a.net", "b.net", "c.net", "d.net"]
    res = check_host_admission(
        _declaring(str(tuple(hosts))),
        registry_hosts={"example.com"},
        evidence_hosts=hosts,
        cap=3,
    )
    assert not res.passed
    assert any("cap" in f for f in res.failures)


def test_admission_rejects_a_kernel_denied_host_even_with_evidence() -> None:
    # Policy is inferred, enforcement stays fixed (§8): evidence can never
    # launder an internal target into the allowlist.
    res = check_host_admission(
        _declaring('("169.254.169.254",)'),
        registry_hosts={"example.com"},
        evidence_hosts=["169.254.169.254"],
        cap=3,
    )
    assert not res.passed
    assert any("SSRF kernel" in f for f in res.failures)


def test_admission_rejects_site_drift() -> None:
    # A winner whose SITE is not the registry entry would derive its whole crawl
    # allowlist from a base the operator never registered — rejected outright.
    src = 'SITE = "https://evil.example"\ndef crawl(ctx):\n    yield 1\n'
    res = check_host_admission(
        src, registry_hosts={"example.com"}, evidence_hosts=[], cap=3
    )
    assert not res.passed
    assert any("registry" in f for f in res.failures)


def test_admission_rejects_an_unparseable_site() -> None:
    # No SITE constant at all: nothing to pin the allowlist to.
    res = check_host_admission(
        "def crawl(ctx):\n    yield 1\n",
        registry_hosts={"example.com"},
        evidence_hosts=[],
        cap=3,
    )
    assert not res.passed
    assert any("parse" in f for f in res.failures)


# -- gate 2: runtime completeness (hard half) --------------------------------


def _good_sample(url: str = "https://example.com/a") -> dict[str, str]:
    return {
        "url": url,
        "title": "Senior Backend Engineer",
        "company": "Acme Corp",
        "location": "Remote",
        "description": "x" * 600,
    }


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
        result, min_items=5, min_description_chars=400, allowed_hosts=_HOSTS
    ).passed


def test_completeness_passes() -> None:
    assert _complete(_result(item_count=10, samples=[_good_sample()]))


def test_too_few_items_fails() -> None:
    assert not _complete(_result(item_count=2, samples=[_good_sample()]))


def test_exception_fails() -> None:
    exc = ExceptionInfo(type="ValueError", message="boom", traceback_tail="...")
    assert not _complete(_result(item_count=9, samples=[_good_sample()], exception=exc))


def test_schema_errors_beyond_tolerance_fail() -> None:
    # 1 error across 10 yields = 10%: a systematic extraction bug, not a wart.
    assert not _complete(
        _result(item_count=9, samples=[_good_sample()], schema_errors=["bad url"])
    )


def test_schema_errors_within_tolerance_pass() -> None:
    # 1 invalid item on a 100-role board: production _collect drops it and the
    # crawl proceeds (errors=1), so admission must not sink the candidate either.
    assert _complete(
        _result(
            item_count=99,
            samples=[_good_sample()],
            schema_errors=["location: String should have at least 1 character"],
        )
    )


def test_empty_title_sample_fails() -> None:
    s = _good_sample()
    s["title"] = "   "
    assert not _complete(_result(item_count=9, samples=[s]))


def test_missing_company_sample_fails() -> None:
    # company is required per sample (§4.6 Job) — a posting with no company is not a
    # trackable job even if title/location/description are fine.
    s = _good_sample()
    s["company"] = "   "
    gate = check_completeness(
        _result(item_count=9, samples=[s]),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("company" in f for f in gate.failures)


def test_missing_location_sample_fails() -> None:
    s = _good_sample()
    s["location"] = ""
    gate = check_completeness(
        _result(item_count=9, samples=[s]),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("location" in f for f in gate.failures)


def test_min_items_one_boundary() -> None:
    # A legitimate small board: a single complete posting clears the floor at
    # min_items=1 (the new default) — completeness is carried by the §6 enum floor
    # and the stability gate, not a large absolute count.
    assert check_completeness(
        _result(item_count=1, samples=[_good_sample()]),
        min_items=1,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    ).passed
    # Zero positions still fails the floor.
    assert not check_completeness(
        _result(item_count=0, samples=[]),
        min_items=1,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    ).passed


def test_short_description_sample_fails() -> None:
    s = _good_sample()
    s["description"] = "too short"
    assert not _complete(_result(item_count=9, samples=[s]))


def test_a_minority_of_short_descriptions_still_passes() -> None:
    # 4 of 5 full, 1 terse (a one-line contract role): the runtime filter drops the
    # terse one, so the gate must not reject the whole crawler over it (§6 aligns with
    # §4.6). Descriptions are distinct so the boilerplate check stays quiet.
    samples: list[dict[str, str]] = [
        {
            "url": f"https://example.com/a{i}",
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": f"Role {i}. " + "x" * 600,
        }
        for i in range(4)
    ]
    samples.append(
        {
            "url": "https://example.com/b",
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "one-line brief",
        }
    )
    assert _complete(_result(item_count=9, samples=samples))


def test_mostly_short_descriptions_fails() -> None:
    # 3 of 5 terse → the crawler mostly yields title+teaser stubs (a listing page it
    # never followed into the full posting); below the 0.6 quality floor, so reject.
    full = [
        {
            "url": f"https://example.com/f{i}",
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": f"Full {i}. " + "x" * 600,
        }
        for i in range(2)
    ]
    shorts = [
        {
            "url": f"https://example.com/s{i}",
            "title": "Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": f"teaser {i}",
        }
        for i in range(3)
    ]
    assert not _complete(_result(item_count=9, samples=full + shorts))


def test_mojibake_description_fails() -> None:
    s = _good_sample()
    s["description"] = "�" * 600
    assert not _complete(_result(item_count=9, samples=[s]))


def test_off_domain_sample_fails() -> None:
    assert not _complete(
        _result(item_count=9, samples=[_good_sample("https://evil.com/a")])
    )


def test_under_enumeration_fails_against_expected_count() -> None:
    # The board lists ~40 openings; yielding 8 under-enumerates even though 8 clears
    # the absolute floor — the crawler must consult the fuller source, e.g. the ATS
    # content API's meta.total rather than one page of a paginated listing (§6).
    gate = check_completeness(
        _result(item_count=8, samples=[_good_sample()]),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
        expected_items=40,
    )
    assert not gate.passed
    assert any("of ~40" in f for f in gate.failures)


def test_enumeration_floor_passes_when_close_enough() -> None:
    # 25 >= ceil(0.5 * 40) = 20 → enough of the board's openings enumerated.
    assert check_completeness(
        _result(item_count=25, samples=[_good_sample()]),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
        expected_items=40,
    ).passed


def test_no_expected_count_keeps_the_enumeration_floor_dormant() -> None:
    # Without a known expected count the relative floor cannot fire (only min_items) —
    # e.g. an ``enumerates_all: false`` source that only shows a recent window.
    assert check_completeness(
        _result(item_count=8, samples=[_good_sample()]),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    ).passed


# -- gate 2: the INVERTED boilerplate check (§6) -----------------------------
# On one company's board every posting legitimately repeats the About-us /
# benefits / EEO slabs. So the gate STRIPS blocks shared by >= 2 samples and
# judges each posting's UNIQUE residual, rather than rejecting a crawler for
# sharing them — the exact spot a mechanical news->jobs port rejects every
# correct crawler (see the gates.py module docstring).

_EEO = (
    "About Acme Corp: we are a team of builders shipping reliable software to "
    "customers around the world. Acme is proud to be an equal opportunity employer; "
    "we do not discriminate on the basis of race, religion, gender, age, or "
    "disability status, and all qualified applicants are considered."
)


def _job(url: str, description: str, *, title: str = "Engineer") -> dict[str, str]:
    return {
        "url": url,
        "title": title,
        "company": "Acme Corp",
        "location": "Remote",
        "description": description,
    }


def test_shared_eeo_block_passes_with_unique_residual() -> None:
    # Two postings that BOTH carry the shared About-us/EEO slab but each has its own
    # substantial role text: the healthy shape of a careers board — must PASS.
    a = _job(
        "https://example.com/a",
        "We are hiring a backend engineer to design, build, and operate our payments "
        "and billing APIs in Python and Go, owning reliability and on-call from the "
        "first commit through the production rollout.\n\n" + _EEO,
        title="Backend Engineer",
    )
    b = _job(
        "https://example.com/b",
        "We are hiring a frontend engineer to build our customer-facing dashboard in "
        "TypeScript and React, partnering closely with design and product on the "
        "end-to-end user experience and accessibility.\n\n" + _EEO,
        title="Frontend Engineer",
    )
    assert check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_description_chars=100,
        allowed_hosts=_HOSTS,
    ).passed


def test_all_boilerplate_fails() -> None:
    # Two postings that are almost entirely the shared slab, with only a scrap of
    # unique text each: after stripping the shared block the residual is tiny, so the
    # crawler is scraping boilerplate as the description — REJECT (the inversion).
    a = _job("https://example.com/a", _EEO + "\n\nRole A")
    b = _job("https://example.com/b", _EEO + "\n\nRole B")
    gate = check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_description_chars=100,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("boilerplate" in f for f in gate.failures)


def test_identical_descriptions_under_different_titles_fail() -> None:
    # A crawler yielding the SAME description for postings with DIFFERENT titles (a
    # page-level generic blurb, not the per-role text) — REJECT. Built from
    # sub-80-char lines so no block registers as shared boilerplate; this isolates
    # the identical-descriptions rule from the residual check.
    line = "We build reliable backend services in Python and Go."
    desc = "\n\n".join(f"{line} Detail {j}." for j in range(6))
    a = _job("https://example.com/a", desc, title="Backend Engineer")
    b = _job("https://example.com/b", desc, title="Frontend Engineer")
    gate = check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_description_chars=100,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("identical descriptions" in f for f in gate.failures)


def test_same_title_region_clones_pass() -> None:
    # One req posted per region as separate postings with byte-identical bodies —
    # the GitLab/Greenhouse shape. The clone pair shares title AND description, so
    # it is tolerated; and the clone's own body (an ≥80-char block appearing in two
    # samples) must NOT count as a shared boilerplate block, or the clones would
    # residual-strip themselves to zero. The third, distinct posting keeps the set
    # from collapsing to a single job.
    clone_desc = (
        "We are hiring a backend engineer to design, build, and operate our payments "
        "and billing APIs in Python and Go, owning reliability and on-call from the "
        "first commit through the production rollout across the region."
    )
    emea = _job("https://example.com/be-emea", clone_desc, title="Backend Engineer")
    amer = _job("https://example.com/be-amer", clone_desc, title="Backend Engineer")
    other = _job(
        "https://example.com/fe",
        "We are hiring a frontend engineer to build our customer-facing dashboard in "
        "TypeScript and React, partnering closely with design and product on the "
        "end-to-end user experience and accessibility for every customer.",
        title="Frontend Engineer",
    )
    gate = check_completeness(
        _result(item_count=9, samples=[emea, amer, other]),
        min_items=5,
        min_description_chars=100,
        allowed_hosts=_HOSTS,
    )
    assert gate.passed, gate.reason


def test_long_shared_intro_with_distinct_role_text_passes() -> None:
    # The GitLab shape that motivated faithful sandbox samples: every posting opens
    # with the SAME ~1.4k-char company intro, then carries substantial role-specific
    # text. On full descriptions the intro is a shared boilerplate block (stripped
    # from the residual) and no pair is byte-identical — the gate must pass. Graded
    # on clipped heads instead, all five would collapse into "identical
    # descriptions" and every honest crawler for the board would be rejected.
    intro = (
        "GitLab is an all-remote DevSecOps company where everyone can contribute. " * 19
    )
    samples = [
        _job(
            f"https://example.com/{i}",
            intro + "\n\n" + f"Role {i}: you will own subsystem {i} end to end. " * 12,
            title=f"Engineer {i}",
        )
        for i in range(5)
    ]
    gate = check_completeness(
        _result(item_count=9, samples=samples),
        min_items=5,
        min_description_chars=400,
        allowed_hosts=_HOSTS,
    )
    assert gate.passed, gate.reason


def test_all_samples_one_posting_fails() -> None:
    # Every sample is the SAME (title, description) — one job echoed n times is a
    # broken enumeration even though the same-title clone rule tolerates each pair.
    line = "We build reliable backend services in Python and Go."
    desc = "\n\n".join(f"{line} Detail {j}." for j in range(6))
    samples = [
        _job(f"https://example.com/{i}", desc, title="Backend Engineer")
        for i in range(3)
    ]
    gate = check_completeness(
        _result(item_count=9, samples=samples),
        min_items=5,
        min_description_chars=100,
        allowed_hosts=_HOSTS,
    )
    assert not gate.passed
    assert any("same posting" in f for f in gate.failures)


def test_defective_indices_track_the_gate_rules() -> None:
    # The §4.11 description axis grades by the same defect set the gate enforces
    # (one _identity_scan feeds both): cross-title identical pairs, the collapsed
    # set, and thin residual are defective; a same-title clone pair is not.
    blurb = "One page-level blurb the crawler stamped on every posting. " * 4
    cross = [
        _job("https://example.com/a", blurb, title="Backend Engineer"),
        _job("https://example.com/b", blurb, title="Frontend Engineer"),
        _job("https://example.com/c", "A genuinely distinct role text. " * 8),
    ]
    assert defective_description_indices(cross, min_unique_chars=150) == {0, 1}

    collapsed = [
        _job(f"https://example.com/{i}", blurb, title="Backend Engineer")
        for i in range(3)
    ]
    assert defective_description_indices(collapsed, min_unique_chars=150) == {0, 1, 2}

    clones = [
        _job("https://example.com/be-1", blurb, title="Backend Engineer"),
        _job("https://example.com/be-2", blurb, title="Backend Engineer"),
        _job("https://example.com/fe", "A genuinely distinct role text. " * 8),
    ]
    assert defective_description_indices(clones, min_unique_chars=150) == set()

    thin = [
        _job("https://example.com/a", _EEO + "\n\nRole A"),
        _job("https://example.com/b", _EEO + "\n\nRole B", title="Data Engineer"),
    ]
    assert defective_description_indices(thin, min_unique_chars=150) == {0, 1}


def test_distinct_descriptions_pass_the_boilerplate_check() -> None:
    a = _good_sample("https://example.com/a")
    b = _job("https://example.com/b", "y" * 600, title="Data Engineer")
    assert check_completeness(
        _result(item_count=9, samples=[a, b]),
        min_items=5,
        min_description_chars=400,
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
