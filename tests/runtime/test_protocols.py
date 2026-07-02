"""Tests for the generated-crawler contract (§4.3).

A committed ``crawlers/<slug>.py`` exposes ``SITE: str`` and ``crawl(ctx) ->
Iterable[Job]``; ``ctx`` is the single fetch boundary. ``load_crawler``
validates a loaded module before the runner calls it.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from jobcrawler.runtime.contract import Ctx, Job, load_crawler


class _StubCtx:
    def fetch(self, url: str) -> str:
        return ""

    def render(self, url: str) -> str:
        return ""

    def get(self, url: str, **kwargs: object) -> object:
        return None

    def log(self, *args: object, **kwargs: object) -> None:
        return None


def test_ctx_protocol_is_satisfied_structurally() -> None:
    assert isinstance(_StubCtx(), Ctx)


def test_incomplete_ctx_is_not_a_ctx() -> None:
    incomplete = SimpleNamespace(fetch=lambda url: "")  # missing render/get/log
    assert not isinstance(incomplete, Ctx)


def test_load_crawler_accepts_a_conforming_module() -> None:
    def crawl(ctx: Ctx) -> Iterable[Job]:
        return []

    module = SimpleNamespace(SITE="https://example.com", crawl=crawl)
    site, fn = load_crawler(module)
    assert site == "https://example.com"
    assert fn is crawl


def test_load_crawler_rejects_missing_site() -> None:
    module = SimpleNamespace(crawl=lambda ctx: [])
    with pytest.raises(TypeError):
        load_crawler(module)


def test_load_crawler_rejects_blank_site() -> None:
    module = SimpleNamespace(SITE="", crawl=lambda ctx: [])
    with pytest.raises(TypeError):
        load_crawler(module)


def test_load_crawler_rejects_missing_crawl() -> None:
    module = SimpleNamespace(SITE="https://example.com")
    with pytest.raises(TypeError):
        load_crawler(module)
