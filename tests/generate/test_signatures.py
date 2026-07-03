"""Tests for the generator signatures (§4.2, §4.11).

GenerateCrawler is the RLM task signature (docstring = instruction, per project
convention); DiscoveryStrategy is the narrow, GEPA-owned strategy predictor whose
seed must enumerate the ordered discovery rules (an optimizer can't infer a rule
set from a vacuous seed). The tests pin the field contract and that the seeds
carry the load-bearing job-board instructions (ATS-API-first, the
anti-under-enumeration discipline), not their exact prose.
"""

from __future__ import annotations

from typing import get_args

import dspy

from jobcrawler.generate.signatures import DiscoveryStrategy, GenerateCrawler


def test_generate_crawler_input_fields() -> None:
    assert set(GenerateCrawler.input_fields) == {
        "site_url",
        "corpus",
        "render",
        "hints",
    }


def test_generate_crawler_output_fields() -> None:
    assert set(GenerateCrawler.output_fields) == {
        "discovery_method",
        "discovery_url",
        "crawler_source",
    }


def test_crawler_source_is_dspy_code() -> None:
    assert GenerateCrawler.output_fields["crawler_source"].annotation is dspy.Code


def test_discovery_method_is_constrained_literal() -> None:
    annotation = GenerateCrawler.output_fields["discovery_method"].annotation
    # 'api' is added for the ATS-first ordering; feed/sitemap/scrape are kept so the
    # scoring/must-observe gates that consume those literals still line up (§6).
    assert set(get_args(annotation)) == {"api", "feed", "sitemap", "scrape"}


def test_generate_crawler_instruction_demands_validate_and_api_first() -> None:
    instr = GenerateCrawler.instructions.lower()
    assert "validate" in instr
    assert "feed" in instr
    # retargeted to job boards: it must crawl postings and prefer a structured ATS
    # API, naming the empirical endpoints the operator probed.
    assert "job" in instr and "posting" in instr
    assert "api" in instr
    for ats in ("greenhouse", "lever", "ashby", "gupy"):
        assert ats in instr


def test_generate_crawler_instruction_states_the_site_admission_rule() -> None:
    # Gate 1(b) rejects a SITE off the registry host; the instruction must state
    # that fixed contract or a model anchors SITE on a board's vanity careers
    # domain (duolingo's Greenhouse board links careers.duolingo.com) and every
    # candidate loses at the gate without ever being told the rule.
    instr = GenerateCrawler.instructions.lower()
    assert "site must be exactly the given site_url" in instr


def test_generate_crawler_instruction_names_structured_job_fields() -> None:
    # The generator must extract-when-present (never fabricate) the structured Job
    # fields, so the docstring has to name them.
    instr = GenerateCrawler.instructions.lower()
    for field in ("salary", "employment_type", "workplace_type", "expires_at"):
        assert field in instr
    assert "company" in instr  # hard-coded from the operator hint on single-co boards


def test_strategy_predictor_fields() -> None:
    assert set(DiscoveryStrategy.input_fields) == {"site_features"}
    assert set(DiscoveryStrategy.output_fields) == {"discovery_plan"}


def test_strategy_seed_enumerates_ordered_rules() -> None:
    instr = DiscoveryStrategy.instructions.lower()
    # not vacuous: the seed enumerates the api-first / validate-before-finalize rules.
    assert "api" in instr  # probe the ATS API first (whole-board enumeration)
    assert "feed" in instr and "sitemap" in instr
    assert "scrap" in instr  # scrape only when nothing enumerates the board
    assert "verify" in instr or "validate" in instr
    assert "board" in instr or "posting" in instr
