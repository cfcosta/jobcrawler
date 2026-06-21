"""Tests for the generator signatures (§4.2, §4.11).

GenerateCrawler is the RLM task signature (docstring = instruction, per project
convention); DiscoveryStrategy is the narrow, GEPA-owned strategy predictor whose
seed must enumerate the ordered discovery rules (an optimizer can't infer a rule
set from a vacuous seed). The tests pin the field contract and that the seeds
carry the load-bearing instructions, not their exact prose.
"""

from __future__ import annotations

from typing import get_args

import dspy

from bizup_autocrawler.generate.signatures import DiscoveryStrategy, GenerateCrawler


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
    assert set(get_args(annotation)) == {"feed", "sitemap", "scrape"}


def test_generate_crawler_instruction_demands_validate_and_feed_first() -> None:
    instr = GenerateCrawler.instructions.lower()
    assert "validate" in instr
    assert "feed" in instr


def test_strategy_predictor_fields() -> None:
    assert set(DiscoveryStrategy.input_fields) == {"site_features"}
    assert set(DiscoveryStrategy.output_fields) == {"discovery_plan"}


def test_strategy_seed_enumerates_ordered_rules() -> None:
    instr = DiscoveryStrategy.instructions.lower()
    # not vacuous: the seed enumerates the feed-first / validate-before-finalize rules.
    assert "feed" in instr and "sitemap" in instr
    assert "scrap" in instr  # scrape only when no feed/sitemap
    assert "verify" in instr or "validate" in instr
