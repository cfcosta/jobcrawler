"""Tests for the Hacker News "Who is Hiring" crawler's apply-link handling.

HN job comments are applied to by email far more often than by a link, and the API
endpoint is not a human-facing URL. So the apply link should be a ``mailto:`` to the
posted address (falling back to a real ATS/careers link when present), and the position's
``url`` should be the HN comment permalink rather than the JSON API endpoint.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import hn_whos_hiring as hn


def test_find_email_plain_and_obfuscated() -> None:
    assert hn._find_email("mail us at jobs@acme.com please") == "jobs@acme.com"
    # HN posters obfuscate to dodge scrapers — the common bracketed forms de-obfuscate.
    assert hn._find_email("careers [at] bigco [dot] io") == "careers@bigco.io"
    assert hn._find_email("team (AT) x (DOT) dev") == "team@x.dev"
    assert hn._find_email("no address here") is None


def _link(raw: str) -> str | None:
    return hn._apply_link(raw, hn._clean(raw))


def test_apply_link_prefers_mailto_for_an_emailed_application() -> None:
    # The core fix: an email application becomes a mailto:, not the HN item URL.
    assert _link("Acme. Email jobs@acme.com to apply.") == "mailto:jobs@acme.com"
    # A bare company homepage does not outrank the email apply channel.
    assert _link("We're at https://acme.com — email hiring@acme.com") == "mailto:hiring@acme.com"


def test_apply_link_prefers_a_real_ats_link_over_email() -> None:
    # A direct application form beats an email.
    assert _link("Apply https://jobs.lever.co/acme/1 or email us@acme.com") == "https://jobs.lever.co/acme/1"


def test_apply_link_reads_a_mailto_href_and_drops_the_subject() -> None:
    raw = 'Reach out: <a href="mailto:team@x.com?subject=Job">team@x.com</a>'
    assert _link(raw) == "mailto:team@x.com"


def test_apply_link_falls_back_to_a_careers_url_then_nothing() -> None:
    assert _link("See https://startup.example/careers for roles") == "https://startup.example/careers"
    assert _link("Great team, DM me on HN.") is None


class _FakeCtx:
    """Feeds the two-step HN Algolia fetch (thread search → thread item) from fixtures."""

    def __init__(self, search: dict, item: dict) -> None:
        self._search, self._item = search, item

    def fetch(self, url: str, **_: object) -> str:
        return json.dumps(self._search if "search_by_date" in url else self._item)

    def render(self, url: str) -> str:  # pragma: no cover - unused
        return ""

    def log(self, *a: object, **k: object) -> None:  # pragma: no cover - unused
        pass


def test_crawl_yields_hn_permalink_urls_and_mailto_apply_links() -> None:
    search = {"hits": [{"title": "Ask HN: Who is hiring? (January 2026)", "objectID": "999"}]}
    item = {
        "title": "Ask HN: Who is hiring? (January 2026)",
        "children": [
            {"id": 111, "author": "alice", "created_at": "2026-01-02T10:00:00Z",
             "text": "Acme | Rust | Remote<p>Email <a href=\"mailto:jobs@acme.com\">jobs@acme.com</a>"},
            {"id": 222, "author": "bob", "created_at": "2026-01-02T11:00:00Z",
             "text": "Globex | Backend | SF<p>Apply at https://jobs.lever.co/globex"},
        ],
    }
    jobs = list(hn.crawl(_FakeCtx(search, item)))
    assert [j["url"] for j in jobs] == [
        "https://news.ycombinator.com/item?id=111",
        "https://news.ycombinator.com/item?id=222",
    ]
    assert jobs[0]["apply_url"] == "mailto:jobs@acme.com"  # emailed application → mailto
    assert jobs[1]["apply_url"] == "https://jobs.lever.co/globex"  # ATS link kept


def test_apply_link_unescapes_algolia_entity_escaped_hrefs() -> None:
    # Algolia's comment_text entity-escapes attribute values ("&#x2F;" for "/",
    # "&amp;" for "&"), so each captured href must be unescaped — otherwise the
    # stored apply_url is garbage and the ATS identity key derived from it (§17)
    # can never match the same posting seen on a direct board.
    raw = (
        "Acme | Rust | Remote<p>Apply here: "
        '<a href="https:&#x2F;&#x2F;boards.greenhouse.io&#x2F;acme&#x2F;jobs&#x2F;'
        '400123?gh_src=hn&amp;t=1" rel="nofollow">apply</a>'
    )
    assert _link(raw) == "https://boards.greenhouse.io/acme/jobs/400123?gh_src=hn&t=1"
