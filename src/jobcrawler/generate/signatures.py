"""DSPy signatures for the generator (§4.2) and the GEPA-owned strategy (§4.11).

Per project convention the **docstring is the task instruction**. Two signatures:

  * ``GenerateCrawler`` — what ``dspy.RLM`` is given: explore the prefetched
    corpus, strongly prefer a structured ATS JSON API (it enumerates the *whole*
    board), emit a contract-implementing module, and validate it before
    submitting. ``crawler_source`` is a ``dspy.Code`` (gate-1 and the write step
    consume ``.code``); ``corpus`` is the JSON-able snapshot.

  * ``DiscoveryStrategy`` — the *narrow* predictor we own and let GEPA optimize
    (``site_features -> discovery_plan``), kept module-agnostic so an RLM->CodeAct
    swap doesn't invalidate it. Its seed enumerates the ordered discovery rules
    (an optimizer can't infer a rule set from a vacuous seed), and ``site_features``
    carries the per-site facts GEPA must *not* memorize — forcing it to evolve
    generalizable strategy rather than hard-code fixture ATS tokens/feed URLs.
"""

from __future__ import annotations

from typing import Literal

import dspy


class GenerateCrawler(dspy.Signature):
    """Write a standalone Python crawler for one JOB BOARD.

    Explore the prefetched ``corpus`` first; fetch more with the tools only if
    needed. Pick the source that enumerates the WHOLE board and yields the FULL
    posting text, strictly in this order:

      1. A structured ATS JSON API — when ``corpus['apis']`` or the site host
         reveals a known ATS, it returns every open role in one place (the
         anti-under-enumeration win). Empirical endpoints + full-text gotchas:
           * Greenhouse: boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
             — whole board in one call (meta.total); ``content`` is the FULL
             description but HTML-entity-ESCAPED, so html.unescape() then strip
             tags; location.name is one flattened string; no salary fields.
           * Lever: api.lever.co/v0/postings/<token>?mode=json — ``description`` is
             the INTRO ONLY; reconstruct the full body as
             description + every lists[].content + additional; workplaceType and
             categories.{location,team,commitment} are always present; createdAt
             is epoch-ms; there is no company field.
           * Ashby: api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true
             — descriptionPlain/descriptionHtml are FULL; employmentType always
             present; structured compensation ~70% (summaryComponents
             minValue/maxValue/currencyCode/interval); prefer workplaceType over
             isRemote; boards can be MBs.
           * Gupy (BR): the <company>.gupy.io board embeds ALL jobs in a
             ``__NEXT_DATA__`` script; or page
             employability-portal.gupy.io/api/v1/jobs?companyId=..&limit=100&offset=..
             (limit max 100 — page until a short page; don't trust total at 100).
      2. JSON-LD ``JobPosting`` objects embedded in a posting page.
      3. An RSS/Atom feed or a jobs sitemap.
      4. Scrape the HTML only when nothing above enumerates the board.

    Emit a module implementing the crawler contract (a module-level SITE constant
    and crawl(ctx) yielding Jobs). SITE must be exactly the given site_url — the
    acceptance gate rejects a SITE on any other host, even the board's own
    careers domain; job *urls*, by contrast, keep the source's canonical posting
    links wherever they live. Yield one Job per open role with url, title,
    company, location, and the FULL description text, plus every structured field
    the source actually states — salary, employment_type, workplace_type,
    department, posted_at, expires_at, external_id, apply_url. NEVER fabricate a
    salary or a date: leave a field unset when the source is silent. On a
    single-company board hard-code ``company`` from the operator hint.

    Validate with validate_crawler before SUBMIT: it must yield several postings,
    each with a non-empty title, a full description, and an on-domain URL. Reason
    in code over the corpus; use llm_query sparingly (a few targeted calls at most
    — never a per-posting or per-URL loop of sub-calls).
    """

    site_url: str = dspy.InputField(desc="job-board root / careers URL")
    corpus: dict = dspy.InputField(
        desc="prefetched {homepage, robots, sitemap, apis, feeds, "
        "postings:[{url,title,text}]} — JSON-able"
    )
    render: str = dspy.InputField(desc="static | browser | solver | auto")
    hints: str = dspy.InputField(desc="optional operator guidance")
    discovery_method: Literal["api", "feed", "sitemap", "scrape"] = dspy.OutputField(
        desc="how postings are discovered"
    )
    discovery_url: str = dspy.OutputField(
        desc="the ATS API / feed / sitemap / index URL used"
    )
    crawler_source: dspy.Code = dspy.OutputField(
        desc="Python module implementing the contract"
    )


class DiscoveryStrategy(dspy.Signature):
    """Decide how to discover JOB POSTINGS for one board, as an ordered rule set.

    1. Probe a structured ATS JSON API BEFORE anything else — Greenhouse, Lever,
       Ashby, and Gupy each expose the WHOLE board as JSON, whereas a homepage
       scrape only samples what's on the first screen. This is the
       anti-under-enumeration discipline: a board with 80 open roles that yields
       12 has failed, however complete those 12 look.
    2. Prefer the source that returns the FULL posting body, in order: a
       structured ATS API — reconstructing the complete text (Lever's
       description + lists[].content + additional; an html.unescape'd Greenhouse
       content; Ashby's descriptionPlain) > JSON-LD JobPosting > a full RSS/Atom
       feed or jobs sitemap > scraping the HTML. An intro-only or truncated body
       is not enough — assemble the whole description.
    3. Choose scrape only when no API, feed, or sitemap enumerates the board.
    4. Extract only the posting itself — exclude boilerplate (nav, "apply"
       widgets, benefits blurbs, related-role rails, cookie banners) from the body.
    5. Pull every structured field the source states (salary, employment/workplace
       type, department, dates, external id, apply url) and NEVER invent one;
       hard-code the company on a single-company board from the operator hint.
    6. Verify several real postings before finalizing — never finalize a plan that
       yields zero, or a handful of a much larger board.
    """

    site_features: str = dspy.InputField(
        desc="per-site facts to reason FROM (detected ATS API candidates, "
        "discovered feed/sitemap URLs, the board's posting-count estimate, HTML "
        "outline, robots) — never hard-code these into the plan"
    )
    discovery_plan: str = dspy.OutputField(
        desc="the ordered discovery decisions to follow for this board"
    )
