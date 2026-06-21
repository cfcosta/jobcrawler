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
from typing import cast

import httpx
import pytest

from bizup_autocrawler.generate.snapshot import (
    REPL_HELPERS,
    build_corpus,
    ensure_json_primitive,
    on_domain_links,
    prefetch_corpus,
)
from bizup_autocrawler.runtime.ctx import Ctx


def test_corpus_has_addressable_shape() -> None:
    corpus = build_corpus(
        homepage="<html>home</html>",
        robots="User-agent: *",
        sitemap="<urlset/>",
        articles=[{"url": "https://x.com/a", "title": "A", "text": "body"}],
    )
    assert set(corpus) == {"homepage", "robots", "sitemap", "articles", "feeds"}
    articles = corpus["articles"]
    assert isinstance(articles, list)
    assert articles[0] == {"url": "https://x.com/a", "title": "A", "text": "body"}


def test_corpus_is_json_serializable() -> None:
    corpus = build_corpus(
        homepage="h", articles=[{"url": "u", "title": "t", "text": "x"}]
    )
    # dspy injects it as a literal; if json.dumps works, the literal step will too.
    assert json.loads(json.dumps(corpus)) == corpus


def test_articles_coerced_to_primitive_fields() -> None:
    corpus = build_corpus(homepage="h", articles=[{"url": 123, "title": None}])
    articles = corpus["articles"]
    assert isinstance(articles, list)
    assert articles[0] == {"url": "123", "title": "", "text": ""}


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


def test_dspy_serializer_rejects_non_json_corpus() -> None:
    # §9/§12 substitute-for-smoke-test: dspy's corpus injection raises on a
    # non-JSON type — the behavior ensure_json_primitive mirrors, verified live.
    from dspy.primitives.python_interpreter import (
        CodeInterpreterError,
        PythonInterpreter,
    )

    class _Weird: ...

    with pytest.raises(CodeInterpreterError):
        PythonInterpreter()._to_json_compatible(_Weird())


def test_on_domain_links_filters_and_dedupes() -> None:
    html = (
        '<a href="/a1">A1</a> <a href="/a2">A2</a> <a href="/a1">dup</a>'
        '<a href="https://evil.com/x">off</a>'
    )
    assert on_domain_links(html, "https://x.com/") == [
        "https://x.com/a1",
        "https://x.com/a2",
    ]


def test_prefetch_corpus_assembles_from_ctx() -> None:
    pages = {
        "/": (
            '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
            '<a href="/a1">A1</a><a href="/a2">A2</a>'
        ),
        "/robots.txt": "User-agent: *",
        "/sitemap.xml": "<urlset/>",
        "/a1": "Body of A1",
        "/a2": "Body of A2",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    ctx = Ctx(
        {"x.com"},
        client=httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ),
    )
    corpus = prefetch_corpus("https://x.com/", ctx)

    assert corpus["robots"] == "User-agent: *"
    assert corpus["sitemap"] == "<urlset/>"
    articles = cast("list[dict[str, str]]", corpus["articles"])
    assert {a["url"] for a in articles} == {"https://x.com/a1", "https://x.com/a2"}
    assert {a["text"] for a in articles} == {"Body of A1", "Body of A2"}
    assert corpus["feeds"] == ["https://x.com/feed.xml"]  # feed-first discovery
