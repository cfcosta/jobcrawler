"""Tests for snapshot/corpus shaping (§4.2, §9).

The generator reasons over a *prefetched* snapshot injected into the RLM REPL as
a real variable. dspy serializes it as a Python literal and raises on any
non-JSON type, so the corpus must be an addressable dict of JSON primitives
(addressable parts beat one blob — fewer tokens). Pyodide lacks grep/sed, so we
ship plain-Python navigation helpers the prompt references.
"""

from __future__ import annotations

import ast
import json

import pytest

from bizup_autocrawler.generate.snapshot import (
    REPL_HELPERS,
    build_corpus,
    ensure_json_primitive,
)


def test_corpus_has_addressable_shape() -> None:
    corpus = build_corpus(
        homepage="<html>home</html>",
        robots="User-agent: *",
        sitemap="<urlset/>",
        articles=[{"url": "https://x.com/a", "title": "A", "text": "body"}],
    )
    assert set(corpus) == {"homepage", "robots", "sitemap", "articles"}
    assert corpus["articles"][0] == {
        "url": "https://x.com/a",
        "title": "A",
        "text": "body",
    }


def test_corpus_is_json_serializable() -> None:
    corpus = build_corpus(
        homepage="h", articles=[{"url": "u", "title": "t", "text": "x"}]
    )
    # dspy injects it as a literal; if json.dumps works, the literal step will too.
    assert json.loads(json.dumps(corpus)) == corpus


def test_articles_coerced_to_primitive_fields() -> None:
    corpus = build_corpus(homepage="h", articles=[{"url": 123, "title": None}])
    art = corpus["articles"][0]
    assert art == {"url": "123", "title": "", "text": ""}


def test_defaults_are_empty_strings_and_list() -> None:
    corpus = build_corpus(homepage="h")
    assert corpus["robots"] == ""
    assert corpus["sitemap"] == ""
    assert corpus["articles"] == []


def test_ensure_json_primitive_accepts_nested_primitives() -> None:
    ensure_json_primitive({"a": [1, 2.0, "x", True, None], "b": {"c": "d"}})  # no raise


def test_ensure_json_primitive_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        ensure_json_primitive({"bad": {1, 2, 3}})  # a set is not JSON-able


def test_repl_helpers_are_valid_python_defining_grep() -> None:
    tree = ast.parse(REPL_HELPERS)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "grep" in defined
