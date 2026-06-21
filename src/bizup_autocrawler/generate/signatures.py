"""DSPy signatures for the generator (§4.2) and the GEPA-owned strategy (§4.11).

Per project convention the **docstring is the task instruction**. Two signatures:

  * ``GenerateCrawler`` — what ``dspy.RLM`` is given: explore the prefetched
    corpus, prefer a feed/sitemap, emit a contract-implementing module, and
    validate it before submitting. ``crawler_source`` is a ``dspy.Code`` (gate-1
    and the write step consume ``.code``); ``corpus`` is the JSON-able snapshot.

  * ``DiscoveryStrategy`` — the *narrow* predictor we own and let GEPA optimize
    (``site_features -> discovery_plan``), kept module-agnostic so an RLM->CodeAct
    swap doesn't invalidate it. Its seed enumerates the ordered discovery rules
    (an optimizer can't infer a rule set from a vacuous seed), and ``site_features``
    carries the per-site facts GEPA must *not* memorize — forcing it to evolve
    generalizable strategy rather than hard-code fixture feed URLs.
"""

from __future__ import annotations

from typing import Literal

import dspy


class GenerateCrawler(dspy.Signature):
    """Write a standalone Python crawler for one news site.

    Explore the prefetched snapshot; fetch more with the tools only if needed.
    Prefer a feed or sitemap; scrape HTML only if neither exists. Emit a module
    implementing the crawler contract (a module-level SITE constant and
    crawl(ctx) yielding items). Validate it with validate_crawler before
    submitting; it must yield several items, each with a non-empty title, body,
    and on-domain URL.
    """

    site_url: str = dspy.InputField(desc="site root / section URL")
    corpus: dict = dspy.InputField(
        desc="prefetched {homepage, robots, sitemap, articles:[{url,title,text}]} — JSON-able"
    )
    render: str = dspy.InputField(desc="static | browser | solver | auto")
    hints: str = dspy.InputField(desc="optional operator guidance")
    discovery_method: Literal["feed", "sitemap", "scrape"] = dspy.OutputField(
        desc="how articles are discovered"
    )
    discovery_url: str = dspy.OutputField(desc="the feed / sitemap / index URL used")
    crawler_source: dspy.Code = dspy.OutputField(
        desc="Python module implementing the contract"
    )


class DiscoveryStrategy(dspy.Signature):
    """Decide how to discover article URLs for a news site, as an ordered rule set.

    1. Probe for a structured content API, feeds, and the sitemap BEFORE scraping
       the DOM — they enumerate articles, whereas a homepage scrape only samples
       "recent".
    2. Prefer the source that returns the FULL article body, in order: a structured
       content API (e.g. a WordPress REST API at /wp-json/wp/v2/posts — full
       content.rendered, paginated with per_page + page) > a full-content feed (RSS
       content:encoded, NOT a short description/excerpt) > freshest sitemap > HTML
       index. A source that returns only an excerpt/summary is not enough — get the
       complete body, paginating where the source supports it.
    3. Choose scrape only when no API, feed, or sitemap is available.
    4. Exclude boilerplate (nav, footer, related-links, share widgets) from
       article bodies.
    5. Verify at least several real items before finalizing — never finalize a
       plan that yields zero or near-zero articles.
    """

    site_features: str = dspy.InputField(
        desc="per-site facts to reason FROM (discovered feed/sitemap URLs, HTML "
        "outline, robots) — never hard-code these into the plan"
    )
    discovery_plan: str = dspy.OutputField(
        desc="the ordered discovery decisions to follow for this site"
    )
