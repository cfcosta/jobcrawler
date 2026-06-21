"""Tests for the crawl runner (§4.4) — the M0 path proof, no LLM.

Drives the real import → crawl → normalize → dedup → ingest path with the
hand-written reference crawler and inline crawlers, using httpx MockTransport
for both the site (ctx) and the news API (ingest). Asserts the run-record
counters, cross-run dedup, §4.4 failure isolation, the §4.7 failure-class
re-attempt policy, and the first-crawl newest-K cap.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from typing import cast

import httpx
from pytest import MonkeyPatch

from bizup_autocrawler.crawlers import example
from bizup_autocrawler.governor import RateGovernor
from bizup_autocrawler.ingest import NewsClient
from bizup_autocrawler.runtime.contract import Ctx as CtxProtocol
from bizup_autocrawler.runtime.contract import Item, load_crawler
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.runner import crawl_site
from bizup_autocrawler.state import StateStore
from bizup_autocrawler.urls import canonicalize_url

UTC = timezone.utc

Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(handler: Handler | None = None) -> Ctx:
    handler = handler or (lambda r: httpx.Response(404))
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return Ctx({"example.com"}, client=client)


def _news(handler: Handler) -> NewsClient:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NewsClient(base_url="http://news.test", client=client)


def _recording_news(status: int = 201) -> tuple[list[dict[str, str]], NewsClient]:
    posts: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(json.loads(request.content))
        return httpx.Response(status)

    return posts, _news(handler)


_INDEX = '<a href="/news/a">Alpha</a> <a href="/news/b">Beta</a>'


def _site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/news":
        return httpx.Response(200, text=_INDEX)
    if path.startswith("/news/"):
        return httpx.Response(200, text=f"<p>Body of {path[-1].upper()}</p>")
    return httpx.Response(404)


def _one(url: str = "https://example.com/a", **kw: object) -> Item:
    fields: dict[str, object] = {
        "url": url,
        "title": "A",
        "body": "x",
        "source": "s",
    }
    fields.update(kw)
    return Item.model_validate(fields)


def test_example_crawler_satisfies_contract() -> None:
    site, _ = load_crawler(example)
    assert site == "https://example.com"
    assert isinstance(_ctx(), CtxProtocol)


def test_happy_path_ingests_each_item() -> None:
    _, crawl = load_crawler(example)
    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("example", crawl, _ctx(_site_handler), store, news)

    assert (summary.items_found, summary.new_items, summary.errors) == (2, 2, 0)
    assert summary.pages_fetched == 3  # index + 2 articles
    assert {p["title"] for p in posts} == {"Alpha", "Beta"}
    assert {p["body"] for p in posts} == {"Body of A", "Body of B"}
    store.close()


def test_second_run_dedups_nothing_new() -> None:
    _, crawl = load_crawler(example)
    store = StateStore(":memory:")
    posts, news = _recording_news()

    crawl_site("example", crawl, _ctx(_site_handler), store, news)
    second = crawl_site("example", crawl, _ctx(_site_handler), store, news)

    assert second.items_found == 2  # still found on the site
    assert second.new_items == 0  # but nothing newly ingested
    assert len(posts) == 2  # no second round of POSTs
    store.close()


def test_run_history_is_recorded() -> None:
    _, crawl = load_crawler(example)
    store = StateStore(":memory:")
    _, news = _recording_news()

    crawl_site("example", crawl, _ctx(_site_handler), store, news)
    rec = store.last_run("example")

    assert rec is not None
    assert rec.finished is not None
    assert (rec.items_found, rec.new_items, rec.pages_fetched) == (2, 2, 3)
    store.close()


def test_script_exception_is_isolated() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a")
        raise RuntimeError("boom after one item")

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert summary.items_found == 1  # the item yielded before the raise
    assert summary.new_items == 1  # was still ingested
    assert summary.errors == 1  # the exception counted as a health signal
    assert len(posts) == 1
    store.close()


def test_non_item_yield_counts_as_error() -> None:
    def crawl(ctx: CtxProtocol) -> Iterator[Item]:
        yield _one("https://example.com/a")
        yield cast(Item, "not an item")  # runtime non-Item exercises the guard

    store = StateStore(":memory:")
    _, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert summary.items_found == 1
    assert summary.errors == 1
    store.close()


def test_dict_yield_is_validated_and_ingested() -> None:
    # Generated crawlers commonly yield plain dicts; the runner must accept them
    # (validating into Item) exactly as the sandbox does — else validate passes
    # but crawl fails. Regression for the live valor.globo.com run.
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield cast(
            Item,
            {
                "url": "https://example.com/a",
                "title": "Headline",
                "body": "x" * 50,
                "source": "example",
            },
        )

    store = StateStore(":memory:")
    posts, news = _recording_news()
    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert (summary.items_found, summary.new_items, summary.errors) == (1, 1, 0)
    assert posts[0] == {"title": "Headline", "body": "x" * 50}
    store.close()


def test_incomplete_bodies_are_not_ingested() -> None:
    # "Only ingest complete" (§4.6): a paywall teaser / truncated body is dropped
    # host-side before ingest, whatever the (untrusted, generated) crawler yields.
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/teaser", body="a short teaser paragraph")
        yield _one("https://example.com/full", body="x" * 600)

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news, min_body_chars=400)

    assert summary.new_items == 1  # only the complete article ingested
    assert summary.incomplete == 1  # the teaser, surfaced for visibility
    assert [p["body"] for p in posts] == ["x" * 600]
    store.close()


def test_incomplete_item_stays_unseen_for_re_evaluation() -> None:
    # A teaser is not marked seen, so once the page carries the full body (the
    # article is un-paywalled / fixed) the next crawl ingests it — no permanent drop.
    served = {"body": "still just a teaser"}

    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a", body=served["body"])

    store = StateStore(":memory:")
    canon = canonicalize_url("https://example.com/a")
    posts, news = _recording_news()

    first = crawl_site("s", crawl, _ctx(), store, news, min_body_chars=400)
    assert (first.new_items, first.incomplete) == (0, 1)
    assert not store.is_seen("s", canon)  # left unseen for re-evaluation

    served["body"] = "x" * 600  # the page now serves the full article
    second = crawl_site("s", crawl, _ctx(), store, news, min_body_chars=400)
    assert second.new_items == 1
    assert [p["body"] for p in posts] == ["x" * 600]
    store.close()


def test_paywall_teaser_is_dropped_even_above_the_length_floor() -> None:
    # A body that clears min_body_chars but carries a subscription pitch is a paywall
    # teaser, not a complete article (Valor's partial paywall). The length floor can't
    # see it; the marker can (§4.6).
    teaser = "Standfirst.\n\n" + "x" * 500 + " Matéria exclusiva para assinantes."

    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/paywalled", body=teaser)
        yield _one("https://example.com/full", body="y" * 600)

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site(
        "s",
        crawl,
        _ctx(),
        store,
        news,
        min_body_chars=400,
        paywall_markers=("exclusiva para assinantes",),
    )

    assert (summary.new_items, summary.incomplete) == (1, 1)
    assert [p["body"] for p in posts] == ["y" * 600]
    store.close()


def test_no_floor_preserves_prior_ingest_behavior() -> None:
    # Default (min_body_chars=0) is off: short bodies still ingest, so the runner's
    # existing callers and the M0 path are unchanged unless completeness is wired.
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a", body="x")

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert (summary.new_items, summary.incomplete) == (1, 0)
    assert len(posts) == 1
    store.close()


def test_terminal_ingest_marks_seen_and_is_not_retried() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a")

    store = StateStore(":memory:")
    canon = canonicalize_url("https://example.com/a")

    first = crawl_site("s", crawl, _ctx(), store, _news(lambda r: httpx.Response(500)))
    assert first.new_items == 0
    assert first.errors == 1
    assert store.is_seen("s", canon)  # terminal-for-cycle → seen, never re-sent

    posts, ok_news = _recording_news()
    second = crawl_site("s", crawl, _ctx(), store, ok_news)
    assert second.new_items == 0  # still skipped even though news is healthy now
    assert posts == []
    store.close()


def test_retry_ingest_stays_unseen_and_is_reattempted() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a")

    store = StateStore(":memory:")
    canon = canonicalize_url("https://example.com/a")

    first = crawl_site("s", crawl, _ctx(), store, _news(lambda r: httpx.Response(400)))
    assert first.new_items == 0
    assert first.errors == 1
    assert not store.is_seen("s", canon)  # pre-insert failure → re-attempt next crawl

    posts, ok_news = _recording_news()
    second = crawl_site("s", crawl, _ctx(), store, ok_news)
    assert second.new_items == 1  # re-attempted and ingested once news recovered
    assert len(posts) == 1
    store.close()


def test_dedup_honors_the_tracking_param_denylist(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_TRACKING_PARAM_DENYLIST", "ref")

    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a?ref=1")
        yield _one("https://example.com/a?ref=2")  # same article, different tracking

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert summary.items_found == 2
    assert summary.new_items == 1  # both collapse to one canonical url
    assert len(posts) == 1
    store.close()


def test_runaway_crawler_is_bounded_by_the_page_budget() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        for i in range(100):
            ctx.fetch(f"https://example.com/{i}")
            yield _one(f"https://example.com/{i}")

    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=False,
    )
    ctx = Ctx({"example.com"}, client=client, max_pages=3)
    store = StateStore(":memory:")
    _posts, news = _recording_news()

    summary = crawl_site("s", crawl, ctx, store, news)

    assert summary.items_found == 3  # only what was yielded before the budget tripped
    assert summary.errors == 1  # PageLimitError isolated to this site
    store.close()


_XXE_FEED = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    "<rss><channel><item><title>&xxe;</title></item></channel></rss>"
)


def test_crawl_site_hardens_untrusted_xml(monkeypatch: MonkeyPatch) -> None:
    # A crawler that parses an attacker-controlled feed with an XXE entity. The
    # runner hardens XML (§8) before running it, so the parse raises and the
    # failure is isolated as a health signal — no item, no exfiltration.
    import bizup_autocrawler.runtime.hardening as hardening

    monkeypatch.setattr(hardening, "_hardened", False)  # force a fresh defuse

    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(_XXE_FEED)  # must raise after harden_xml()
        title = root.find("channel/item/title")
        yield _one(
            "https://example.com/a", title=title.text if title is not None else ""
        )

    store = StateStore(":memory:")
    posts, news = _recording_news()
    summary = crawl_site("s", crawl, _ctx(), store, news)

    assert summary.items_found == 0  # the malicious parse was blocked
    assert summary.errors == 1  # isolated, not a fleet-aborting crash
    assert posts == []
    store.close()


class _GovClock:
    """A fake clock whose ``sleep`` advances it, recording the durations."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d


def test_ingest_is_paced_by_the_rate_governor() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a")
        yield _one("https://example.com/b")

    store = StateStore(":memory:")
    posts, news = _recording_news()
    clock = _GovClock()
    gov = RateGovernor("2/s", clock=clock.now, sleep=clock.sleep)  # 0.5s apart

    summary = crawl_site("s", crawl, _ctx(), store, news, governor=gov)

    assert summary.new_items == 2
    assert clock.sleeps == [0.5]  # first POST is free, the second is paced
    store.close()


def test_governor_paces_only_real_posts_not_skipped_items() -> None:
    # 'a' is already seen, so only 'b' is POSTed — a single POST never sleeps.
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        yield _one("https://example.com/a")
        yield _one("https://example.com/b")

    store = StateStore(":memory:")
    store.mark_ingested("s", canonicalize_url("https://example.com/a"))
    posts, news = _recording_news()
    clock = _GovClock()
    gov = RateGovernor("2/s", clock=clock.now, sleep=clock.sleep)

    summary = crawl_site("s", crawl, _ctx(), store, news, governor=gov)

    assert summary.new_items == 1  # only 'b'
    assert clock.sleeps == []  # one POST → the dedup-skipped 'a' costs no budget
    store.close()


def test_first_crawl_cap_keeps_newest_and_skips_backlog() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Item]:
        for i in range(5):
            yield _one(
                f"https://example.com/{i}",
                title=f"T{i}",
                published_at=datetime(2026, 1, 1 + i, tzinfo=UTC),
            )

    store = StateStore(":memory:")
    posts, news = _recording_news()

    summary = crawl_site("s", crawl, _ctx(), store, news, first_crawl_cap=2)

    assert summary.items_found == 5
    assert summary.new_items == 2  # only the newest 2 ingested
    assert {p["title"] for p in posts} == {"T4", "T3"}
    # the older 3 are marked seen, so a second crawl backfills nothing.
    posts2, news2 = _recording_news()
    second = crawl_site("s", crawl, _ctx(), store, news2)
    assert second.new_items == 0
    assert posts2 == []
    store.close()
