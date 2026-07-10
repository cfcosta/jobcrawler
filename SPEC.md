# jobcrawler — SPEC

| | |
|---|---|
| **Status** | Draft |
| **Date** | 2026-07-02 |
| **Depends on** | nothing external — the old `bizup-news` ingestion target is **gone** (clean break, §4.7) |
| **Core tech** | `dspy.RLM` generation + `GEPA` self-improvement (DSPy 3.2.x) + a host-side `dspy.Predict` profile matcher |

## 1. Summary

`jobcrawler` turns a **list of careers pages / ATS boards** into a fleet of **per-site
crawler scripts** that continuously **track open engineering positions** and **match them
against the operator's `PROFILE.md`**. A `dspy.RLM` agent does the hard reasoning **once
per site** — it explores a prefetched snapshot of the board in a sandboxed REPL (fetching
more on demand via host tools), finds the cleanest way to enumerate postings (a public ATS
JSON API where one exists, an HTML scrape only where none does), and **writes a standalone
Python crawler script** for that board. Thereafter the script runs on a schedule with **no
LLM in the loop**, yielding `Job` records that are deduplicated, **lifecycle-tracked**
(open → changed → closed, reopened, expired), and — for new/changed postings — scored
against the profile by a **host-side, batched, cached LLM matcher** that runs *outside* the
crawl sandbox. Strong matches notify the operator.

The load-bearing invariant is unchanged from the news era, just sharper: **the crawl loop
never calls a model.** Committed crawlers are plain, deterministic Python; host-side
enrichment (discipline/level/workplace/salary) is pure functions (§4.6). There are exactly
**two out-of-loop LLM surfaces**, both bounded and off the hot path:

1. **Generation (§4.2)** — the RLM writes a crawler once per site, and **improves over
   time** through an offline optimization loop (§4.11).
2. **Profile matching (§4.7)** — the *only new* LLM surface versus the news design. It runs
   **host-side, batched, and cache-keyed** on `(content_hash, profile_hash, model,
   prompt_version)`, with a hard daily token cap and a pending-backlog overflow, so scoring
   the daily job fleet is cheap and a matcher outage degrades to "not scored yet", never a
   crawl crash. It replaces the old `POST /news` sink.

Matching *bills tokens*, so it is deliberately parsimonious: a deterministic prefilter drops
definite mismatches for free, a content-addressed cache scores each posting **at most once
ever**, and misses are batched into one prediction per `batch` jobs (§4.7/§8).

## 2. Goals / Non-goals

**Goals (v1)**
- Given a board URL, automatically generate a committed, reviewable `crawlers/<slug>.py`.
- **Prefer a public ATS JSON API** (Greenhouse / Lever / Ashby / Gupy — §4.2/§15) that
  enumerates the *whole* board in a call or two; fall back to HTML scraping only where no
  API exists.
- Yield a rich, typed `Job` (title/company/location/description + opportunistic salary,
  workplace, employment type, timestamps — §4.6), never inventing fields the board didn't
  state.
- **Own the run loop**: a built-in scheduler decides when each board is due and runs its
  script (bounded concurrency, retry/backoff), then dedups and **tracks each position's
  lifecycle** — first seen, content changes, closure when it disappears from a
  fully-enumerating board, and expiry (§4.7).
- **Match every new/changed position against `PROFILE.md`** with a host-side LLM, cache the
  verdict, and **notify** the strong ones (§4.7).
- Detect when a script has gone stale (board changed) and **regenerate** it (§4.8).

**Non-goals (v1)**
- **Auto-applying to jobs.** `jobcrawler` surfaces and ranks openings; a human applies. No
  form-filling, résumé submission, or outreach automation. `apply_url` is stored for the
  operator's convenience, never actioned.
- **Auth-walled boards.** LinkedIn, Indeed, Glassdoor and anything behind a login/consumer
  anti-bot wall are out of scope — we track public ATS boards and careers pages. Logins,
  paywalls, and **interactive** CAPTCHA solving (Turnstile/hCaptcha) remain non-goals;
  passive Cloudflare JS challenges *are* handled transparently (§4.10).
- **Semantic cross-board dedup / repost clustering.** *Superseded by the §17 read-derived
  overlay:* rows stay separate (one per `(site, url)`, content-hash dedup still within that
  key only); identity is clustered at **read time** from deterministic key tiers. What stays
  out: semantic/embedding similarity, LLM-judged identity, and physical row merging.
- **Salary inference.** We parse a salary the board *prints* (§4.6) and never model or
  impute one it doesn't (§14 Q3).
- **Multi-profile / team use.** One `PROFILE.md` per deployment in v1 (§13 next).
- A general-purpose crawler config UI. The board list is a checked-in file.

## 3. Core principle: two phases, and a crawl loop with no LLM

```
            ┌─────────────────────── GENERATION (LLM, once per site) ──────────────────────┐
 site_url ─▶│  dspy.RLM root planner                                                        │
            │    explores via host tools: fetch() · render() · find_feeds() · get_sitemap() │
            │    PREFERS a public ATS JSON API (Greenhouse/Lever/Ashby/Gupy); scrapes only  │
            │    if none — drafts crawler source, validate_crawler() runs it for real &      │
            │    reports, iterates until postings look right ─▶ SUBMIT(crawler_source)       │
            └───────────────────────────────────┬──────────────────────────────────────────┘
                                                 ▼
                          crawlers/<slug>.py   (committed; reviewed or auto-promoted, §8)
                                                 │
   ┌──────────────────────────────── CRAWL (no LLM, scheduled) ─────────────────────────────┐
   │  runner imports module ─▶ crawl(ctx) yields Job(s)                                      │
   │    ctx enforces robots · rate-limit · timeouts · allowed-hosts (script can't bypass)    │
   │    ─▶ validate Job ─▶ canonicalize url ─▶ completeness floor + expired-marker drop      │
   │    ─▶ content_hash ─▶ upsert_position (new/changed/unchanged · REOPEN)                  │
   │    ─▶ close_missing (only on a healthy full enumeration) ─▶ enrich ─▶ prefilter         │
   └──────────────────────────────────────┬─────────────────────────────────────────────────┘
                                          ▼  (new/changed, prefilter-surviving)
   ┌───────── MATCH (host-side LLM, batched + cached + capped, OUTSIDE the sandbox) ─────────┐
   │  cache lookup (hit = free) ─▶ daily-cap split (overflow → match_pending) ─▶             │
   │  dspy.Predict(MatchJobs) vs PROFILE.md ─▶ MatchVerdict{score,verdict,reasons} ─▶ store  │
   │  ─▶ notify strong matches (score ≥ match_min_score, event 'match')                      │
   └─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
        health signals (positions_found, description_quality) ─▶ stale? ─▶ re-enter GENERATION
```

The **generated script declares _what_ to fetch and _how_ to extract**; the **runner owns
_politeness, limits, lifecycle, and matching_ centrally**. A generated script therefore
cannot hammer a board, reach the network except through the supplied `ctx`, decide a
position is closed, or spend a match token.

The crawl phase is **driven by a built-in scheduler** (§4.9), not an external cron:
`jobcrawler` runs as a long-running service that decides when each board is due, tracks
lifecycle, drives matching, and regenerates stale crawlers — one orchestrator over
generation, crawling, matching, and self-healing.

## 4. Components

### 4.1 Site registry (input)
A checked-in list (`sites.yaml`) is the system's input. One entry per board:

| field | type | notes |
|---|---|---|
| `slug` | str | stable id; names `crawlers/<slug>.py` and the `source` field on jobs; must be a valid Python identifier (it names an importable module) |
| `name` | str | human label |
| `url` | str | board root / careers URL |
| `render` | `auto \| static \| browser \| solver` | **max** fetch tier `ctx` may escalate to (§4.10); `auto` lets generation decide |
| `company` | str? | **single-company boards only.** A Greenhouse/Lever/Ashby/Gupy board is one employer, and some APIs omit the company name (Lever has no company field, §15); when set, the generator hard-codes it on every `Job` so `company` (a required field, §4.6) is always populated. Omit for aggregators (RemoteOK/WWR) where each posting carries its own employer. |
| `enumerates_all` | bool = `True` | **does one crawl see the *whole* board?** `True` for an ATS API or a complete per-category feed — closure-by-diff (§4.7) is sound. `False` for a source that only ever shows a recent window (RemoteOK's latest-~100, a combined RSS feed): a disappeared URL there means "scrolled off", **not** "closed", so the §4.7 enumerate-diff is **skipped** and the §4.9 recheck loop covers closure instead. |
| `hints` | str? | optional free-text steer for the generator ("board is Greenhouse token `acme`") |
| `schedule` | str? | crawl cadence — `4h`, `1d`, or `adaptive`; defaults to the fleet cadence (§4.9) |
| `adaptive` | bool | let the §4.9 hit-rate controller learn this board's interval (`schedule: adaptive` is sugar for it) |
| `enabled` | bool | crawl on/off without deleting |

### 4.2 Generator (the RLM phase)

**Module choice.** The generator writes and *runs* Python iteratively, so it needs a
code-execution loop, not just discrete tool calls. `dspy.RLM` fits: a careers page or a
multi-MB Ashby payload is large, semi-structured data best explored *with code + recursive
sub-LLM calls* in a persistent REPL — RLM's core strength. The caveat to keep honest: an
agent-driving-external-tools loop is normally `dspy.ReAct`/`dspy.CodeAct` territory, so we
keep RLM **idiomatic** by handing it a pre-fetched board snapshot as the large input it
reasons over in-REPL, using host tools only for *follow-up* fetches. RLM stays the
**primary** generator; for self-improvement (§4.11) we do *not* invert to CodeAct but wrap
the RLM in a `CrawlerGenerator(dspy.Module)` and point the optimizer at a named predictor we
own. `dspy.CodeAct` ("writes Python that calls your tools") remains the documented fallback
if that predictor optimizes unevenly.

**RLM vs CodeAct is empirically close, not RLM-obviously-better.** The load-bearing property
is *write → run → iterate code over an environment*, which both satisfy; head-to-head,
coding agents match or beat RLM on single-document tasks, recursion adds only ~6 pts
(sometimes *negative* on strong frontier models), and RLM is *worse* than a plain call below
a ~2¹⁴-token crossover [Coding-Agents, SRLM, RLM]. So: **gate recursion on snapshot size**
(prefer the non-recursive / CodeAct path when the snapshot fits one context — the common
single-company-board case; reserve recursive `llm_query` for genuinely large corpora like a
paginated Gupy board or a multi-MB Ashby dump); add a **structured-source fast-path** that
skips DOM exploration/recursion when a public ATS JSON API is found (it still runs *one*
`validate_crawler` pass before `SUBMIT`, satisfying gate-2 must-observe — sandbox-only, no
LLM); keep the owned strategy predictor (§4.11) **module-agnostic** so an RLM→CodeAct swap
doesn't invalidate it; and add a production-backbone **A/B** (RLM-recursive vs non-recursive
CodeAct) on the held-out split in M1/M5, keeping the per-backbone winner. Define the fallback
trigger *operationally*: the GEPA candidate fails to beat the incumbent across K budgets, or
per-gate pass-rates regress on a subset. Keep **depth-1** recursion (sub-calls are leaf LMs;
host tools are the follow-up channel) and async.

**ATS-API-first discovery is the whole strategy, and it is empirically grounded.** A live
probe of the major backends (2026-07-02, §15) established that the cleanest enumeration of a
board is almost always a **public JSON API**, not the rendered HTML — so the seeded strategy
(§4.11) probes for one *before* any DOM scraping. The generator's seed rules encode the probe
findings and their **full-text reconstruction gotchas**, because "grab the description field"
is wrong on two of the four backends:

- **Greenhouse** — `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` returns the
  **whole board in one call** with a `meta.total` count (the §6 enum-floor oracle). `content`
  is the **full** description but **HTML-entity-escaped** — the crawler must `html.unescape`
  *then* strip tags, or every posting is mojibake. No employment fields, but appending
  `&pay_transparency=true` to the SAME call serves per-job `pay_input_ranges`
  `[{min_cents, max_cents, currency_type}]` on many boards (2026-07 fleet probe: 21 of our 60
  boards populated, 2,235 jobs — anthropic 341/401, remotecom 258/279, reddit 124/179; map
  cents/100, single-currency boards only). `location.name` is one flattened string;
  `first_published` is always present (→ `posted_at`).
- **Lever** — `api.lever.co/v0/postings/<token>?mode=json`. The `description` field is the
  **intro only**; the full text is `description + lists[].content + additional` concatenated
  — a crawler that reads `description` alone yields truncated postings that fail the §6
  completeness gate. `workplaceType` is always present (→ `workplace_type`);
  `categories.{location,team,commitment}` map to location/department/employment_type;
  `createdAt` is epoch-**ms**. **No company field** — set it from `sites.company` (§4.1).
- **Ashby** — `api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true`.
  `descriptionPlain`/`descriptionHtml` are **full**; `employmentType` is always present;
  structured compensation is present **~70%** of the time
  (`compensation.summaryComponents[].{minValue,maxValue,currencyCode,interval}` →
  `salary_min/max/currency/period`). **`isRemote` means "remote *allowed*"**, not "remote
  only" — prefer the explicit `workplaceType`. Boards can be **multiple MB** (fold into the
  §4.5 payload note and the recursion gate).
- **Gupy (BR)** — a `<company>.gupy.io` board embeds **all** jobs in `__NEXT_DATA__`; the
  cleaner path is `employability-portal.gupy.io/api/v1/jobs?companyId=..&limit=100&offset=..`,
  which paginates (**limit max 100; do not trust `total` at limit=100 — page until a short
  page**). The `type` enum distinguishes `vacancy_type_effective` (CLT) /
  `vacancy_legal_entity` (PJ) / etc → `employment_type`; `publishedDate` + `expiresAt` map to
  `posted_at`/`expires_at`; salary is absent.
- **Aggregators** — **RemoteOK** `/api` returns **only the latest ~100** (partial → `sites`
  must set `enumerates_all: false`), `element[0]` is a legal-notice object to **skip**, needs a
  browser User-Agent, and its salary keys are **always 0** (drop them, don't store 0).
  **WeWorkRemotely** exposes **complete per-category RSS feeds** (the *combined* feed is capped
  at 10/category, so crawl per-category); `expires_at` is present; `company` is parsed from the
  `'Company: Title'` item-title prefix.

A DSPy signature whose **docstring is the task instruction** (per project convention), driven
by `dspy.RLM`. Illustrative shape (not implementation):

```python
class GenerateCrawler(dspy.Signature):
    """Write a standalone Python crawler for one job board.

    Explore the prefetched snapshot; fetch more with the tools only if needed.
    PREFER a public ATS JSON API (Greenhouse/Lever/Ashby/Gupy) that enumerates the
    whole board; scrape HTML only if none exists. Reconstruct the FULL description
    (Lever = description+lists+additional; Greenhouse = html.unescape then strip).
    Emit a module implementing the crawler contract (§5). Validate it with
    validate_crawler before submitting; it must yield several postings, each with a
    non-empty title, company, location, and a sufficiently long description."""

    site_url: str          = dspy.InputField(desc="board root / careers URL")
    corpus: dict           = dspy.InputField(desc="prefetched {homepage, robots, sitemap, postings:[{url,title,text}]} — JSON-able")
    render: str            = dspy.InputField(desc="static | browser | solver | auto")
    company: str           = dspy.InputField(desc="single-company board employer, or '' for an aggregator")
    hints: str             = dspy.InputField(desc="optional operator guidance")
    discovery_method: Literal["ats_api", "feed", "sitemap", "scrape"] = dspy.OutputField(desc="how postings are discovered")
    discovery_url: str     = dspy.OutputField(desc="the API / feed / sitemap / index URL used")
    crawler_source: dspy.Code = dspy.OutputField(desc="Python module implementing the contract")

# crawler_source is a dspy.Code (markdown fences auto-stripped); gate-1 and the write step
# consume crawler_source.code. corpus is a plain JSON-able dict (§9): dspy injects it into the
# REPL as a real variable (the model sees a truncated preview, reads full parts via code).
# Keep every part JSON-primitive — no live lxml trees / httpx clients / closures.
#
# The signature is wrapped in CrawlerGenerator(dspy.Module) (§4.11) so its predictors are
# visible to the optimizer and to save/load; the scheduler/CLI call it via .acall(...).
```

**Host tools passed to RLM** (`tools=[...]`, plain callables that run on the *host*, with the
real network access the Pyodide sandbox lacks). The prefetched corpus covers the common case;
these handle *follow-up* fetches — API pagination, a deeper posting, a discovered feed:

| tool | purpose |
|---|---|
| `fetch(url)` | static GET → cleaned text/markdown (httpx + markitdown), rate-limited |
| `render(url)` | headless GET → rendered HTML (Playwright) |
| `find_feeds(url)` | discover `<link rel="alternate">` RSS/Atom/JSON feeds |
| `get_sitemap(url)` | fetch & parse `sitemap.xml` (and robots-declared sitemaps) |
| `validate_crawler(source)` | fence-strip, then run the candidate in the execution sandbox (§4.4) **through `ctx`** — against the live board during generation, or in the offline loop against the persisted `corpus` + recorded HTTP via a replay-backed `ctx` (§4.11), *never the network*. Returns a **size-bounded** `CandidateResult` (capped `samples` whose descriptions the *tool* clips to marked previews, condensed `schema_errors`, `exception` = type+message+traceback *tail*) so it survives RLM's output truncation (a **10k-char total** budget when output exceeds 10k — 5k head + 5k tail; keep load-bearing fields in the head), crossing the bridge as JSON the model parses in-REPL. The preview clip is presentation only: the sandbox ships descriptions whole (pipe-bounded far above real posting text), and the §6 gate + §4.11 axes grade that full text — grading a clipped head would collapse boards that stamp a long shared intro on every role (GitLab's runs ~1.4k chars) into "identical descriptions" and reject honest crawlers. |

`validate_crawler` runs the candidate **on the host**, and that's mandatory, not just
convenient: RLM's Pyodide sandbox has no network and none of the crawler's libraries
(`httpx`/`lxml`/`selectolax`/`playwright`), so the host execution sandbox (§4.4) is the only
place a generated crawler can actually run. The model iterates write → `validate_crawler` →
fix before `SUBMIT`.

`validate_crawler` (and the reward core) run each candidate under the **admission-shaped
allowlist** — registry host + that candidate's *declared, evidence-backed* `EXTRA_HOSTS`
(`gates.admissible_hosts`, the same rule as gate 1(b)) — not the wider exploration envelope
the `fetch` tool probes with. Exploration and admission are deliberately different sets;
validating against exploration let a candidate that fetched a cross-host board API without
declaring it (Gupy's `employability-portal`) score full marks, win the fan-out, and only
then die at admission. Under the committed shape the same mistake fails inside the
write → validate → fix loop with an `SsrfError` naming the missing host.

**LMs & budgets.** A strong code-capable **root LM** plans and extracts; a cheap **`sub_lm`**
answers in-sandbox `llm_query` calls — pass it explicitly as a concrete LM (an unset `sub_lm`
silently falls back to the *root* LM, billing every `llm_query` at planner rates;
`require_sub_model` errors rather than allow it, §11). Scope the LM per call with
`with dspy.context(lm=root_lm): await gen.acall(...)`, never a process-global `dspy.configure`
(it is thread-owned and raises from the scheduler's worker tasks — §9). Candidate
**distinctness does not come from `cache=False`**: `BestOfN`/`Refine` run the module N times
each with a distinct **`rollout_id` at temperature=1.0** (`lm.copy(rollout_id=…,
temperature=1.0)`), and `rollout_id` *is* part of the cache key — so attempts re-reason and
miss the cache even with caching **on**; GEPA instead samples by mutating the instruction via
the reflection LM. The real lever for stochastic re-sampling is **temperature > 0** (at
temperature 0 `rollout_id` is inert). Set both RLM caps explicitly (`JOBCRAWLER_MAX_ITERATIONS`,
`JOBCRAWLER_MAX_LLM_CALLS`): `max_llm_calls` raises a *recoverable* in-sandbox error at its
limit (the model can catch it and keep going), so it is a **soft** cap, not a hard
runtime/spend bound — pair a bounded `max_iterations` with the host-side ceilings below,
especially under `BestOfN`/`Refine`/GEPA multiplication. The default sandbox needs **Deno**
installed.

**Hard cost ceilings (host-side backstops).** The installed RLM is bounded *both* ways — the
root loop is `for _ in range(max_iterations)` (default 20) with an extract-fallback on
exhaustion, and `max_llm_calls` caps sub-calls — so `JOBCRAWLER_MAX_ITERATIONS`/`MAX_LLM_CALLS`
map to `dspy.RLM(max_iterations=…, max_llm_calls=…)`, which RLM enforces **internally** as hard
caps — not a *separate* host layer (only `MAX_SUBCALLS` is). Two real gaps remain:
`max_llm_calls` is **soft** (a recoverable error the model can catch and loop past), and
**fan-out** multiplies whole generations, which no single RLM cap bounds. So the host adds an
**unconditional** sub-call kill (`JOBCRAWLER_MAX_SUBCALLS`, set ≥ `max_llm_calls`) that aborts
the trajectory regardless — the *only* hard stop on a catch-and-continue sub-call loop — and an
aggregate `FANOUT_TOKEN_CAP` across the fan-out; plus a one-line anti-over-subcall prompt guard.
(Size the token cap against the **p95 tail**, not the mean — dspy records tokens for every
provider, whereas USD pricing is missing for many OpenRouter/Codex slugs.) The Pyodide/Deno sandbox adds ~2–3 s
cold-start + per-call bridge tax per loop [Sanjaya] — confirm RLM reuses one warm sandbox per
generation, else fold that into the cap.

**Output handling:** `crawler_source` is written to `crawlers/<slug>.py` (and, depending on the
review policy, surfaced for human review or auto-promoted — see §8) only after it passes the §6
acceptance gates.

### 4.3 Crawler script contract (the generated artifact)
Every generated module exposes a fixed interface so one runner can execute any of them:

```python
SITE = "https://boards.greenhouse.io/acme"   # module-level constant
EXTRA_HOSTS = ("api.getro.com",)             # optional; evidence-gated (§6 gate-1(b), §8)

def crawl(ctx) -> Iterable[Job]:              # yields normalized jobs; no network except via ctx
    ...
```

- `ctx` provides `ctx.fetch(url, method=, json=, headers=)`, `ctx.render(url)`,
  `ctx.get(url, **kw)`, `ctx.log(...)`. **All fetching goes through `ctx`** so limits are
  enforced (parsing is the module's own — via the allowlist below — not a `ctx` concern).
  `load_crawler(module)` returns `(SITE, crawl)`, validating a non-empty `SITE: str` and a
  callable `crawl`.
- **`EXTRA_HOSTS` (optional)** — a literal tuple/list of bare hostnames the crawler may fetch
  beyond `SITE`'s host (a cross-host board API, §8). Constants-only by construction: the
  crawl-time allowlist is **AST-read** from the committed source (`contract.allowed_hosts_of`
  = `SITE`'s host ∪ `EXTRA_HOSTS`), never computed at exec time — so the allowlist a crawler
  runs under is exactly the one the §6 host-admission gate judged (evidence-backed, capped,
  SSRF-kernel-clean).
- The module may `import` only from an allowlist (parsers like `selectolax`/`lxml`,
  `feedparser`, stdlib `re`/`json`/`html`/`datetime`/`urllib.parse`). No `os`, `subprocess`,
  `open`, `socket`, `eval`/`exec` (enforced by AST check, §8).
- **Field extraction is the crawler's job**, via the allowlisted parsers with per-board
  selectors / JSON-key reads (their brittleness is what §4.8 drift-detection + regeneration
  handles). The crawler yields **raw** facts (title, company, location, description, and any
  optional fields the source states) — it **never** classifies discipline/level/workplace or
  parses salary text into numbers; that deterministic enrichment is host-side (§4.6/§8). For a
  single-company board it hard-codes `sites.company` on every `Job`.

### 4.4 Runner & execution sandbox
- Imports `crawlers/<slug>.py`, calls `crawl(ctx)`, and consumes the yielded `Job`s.
- Runs the module under resource limits: wall-clock timeout, max pages, memory cap, no
  filesystem writes, network egress **only** through the `ctx` proxy. In production a committed
  crawler runs in a **bounded subprocess** (`crawl_site_isolated`): AST gate + `RLIMIT_AS` +
  `RLIMIT_CPU` (decoupled from wall-clock — a polite crawl is I/O-bound and sleeps between paced
  fetches, so CPU stays tight to catch a spin while wall-clock covers the paced fetches) + hard
  kill. A runaway crawler (infinite loop, memory blowup, hang) is bounded and reported as a
  failed run (0 positions, ≥1 error), **never** allowed to wedge or OOM the daemon.
- A failing script (exception, zero positions, schema violation) is isolated to its board and
  recorded as a health signal — it never aborts the whole crawl, and a failed/empty run **never
  advances closure** (the §4.7 mass-closure guard).
- `validate_crawler` and committed crawlers run in a **single pinned, reused** general-purpose
  sandbox image (Python + httpx/lxml/selectolax/feedparser/playwright), not a per-board image,
  with a soft timeout + hard kill and **no network except the `ctx` egress-allowlist** —
  container isolation alone is necessary-but-not-sufficient when exploring untrusted board
  content (§8). Acceptance is LLM-bound, not sandbox-bound [LLM-in-Sandbox], so the budget knob
  is RLM call count, not container count.

### 4.5 Politeness & fetching (`ctx`, shared by all crawls)
Centralized so generated scripts can't get it wrong: `robots.txt` compliance, per-host rate
limiting + concurrency caps, retries with backoff (transient 429/5xx/dropped-connection only —
a Cloudflare challenge escalates, it is never blindly retried), conditional GET (ETag /
Last-Modified), request timeouts, and an identifying `User-Agent`. `ctx.fetch`/`render`
restrict requests to the board's host(s) and on-domain links (SSRF guard, §8). On a detected
Cloudflare challenge, `ctx.fetch` transparently escalates to the browser/solver tier and caches
the clearance cookie (§4.10) — scripts stay oblivious. `ctx.fetch`/`render` also **resolve text
encoding** (HTTP charset → meta charset/BOM → `charset-normalizer` fallback) and hand the
crawler UTF-8 `str`, so per-board code never guesses charset.

`ctx.fetch` is **method-general** (§8): `method=`/`json=`/`headers=` cover the JSON board APIs
whose enumeration endpoint only answers POST (a search API — an idempotent read in POST
clothing), riding exactly the same SSRF-per-hop, robots, pacing, page-budget and
transient-retry path as a GET — the crawler chooses the verb, never the enforcement. Redirects
follow browser convention: 301/302/303 downgrade a non-GET replay to a **body-less GET**;
307/308 replay method + body; every hop is SSRF-rechecked either way.

**Note — multi-MB board payloads.** Unlike a news homepage, some ATS payloads are large: an
Ashby board can be **several MB** of JSON, and a paginated Gupy board is many 100-row pages. The
per-crawl page/byte budget and the `max_pages` cap are sized with that in mind, and a single
huge fetch is streamed/bounded rather than buffered unboundedly. RemoteOK requires a **browser
User-Agent** for its `/api` — a per-board UA override rides on the fetch tier, not the crawler.

### 4.6 The `Job` model & host-side enrichment
Jobs yielded by scripts are validated against a Pydantic model (`runtime/contract.py`). The
split is deliberate and load-bearing: a small set of **required, non-empty** identity/content
fields, and a wide set of **optional, extract-when-present** fields — because ATS boards differ
wildly in what they expose (Greenhouse serves pay only behind `pay_transparency=true` and no
employment fields; Lever always has `workplaceType`; Ashby has structured compensation ~70% of
the time). The optional fields are
opportunistic enrichment, **never** a contract the generator must satisfy.

| field | type | required | notes |
|---|---|---|---|
| `url` | str | ✓ | canonicalized; the dedup key |
| `title` | str | ✓ | |
| `company` | str | ✓ | single-company boards: hard-coded from `sites.company` (§4.1) |
| `location` | str | ✓ | as stated (often one flattened string) |
| `description` | str | ✓ | the **full** posting text (§4.2 reconstruction rules) |
| `source` | str | ✓ | the board `slug` |
| `salary` | str? | – | **verbatim** printed compensation text |
| `salary_min` / `salary_max` | float? | – | set only from source-**structured** compensation |
| `salary_currency` | str? | – | ISO-ish code as stated |
| `salary_period` | str? | – | `year` \| `month` \| `hour`, as stated |
| `employment_type` | str? | – | raw (CLT/PJ/full-time/contract…) |
| `workplace_type` | str? | – | raw `remote`/`hybrid`/`onsite` as stated |
| `department` | str? | – | team / category |
| `posted_at` / `expires_at` | datetime? | – | drive §4.9 recheck + expiry closure |
| `external_id` | str? | – | stable ATS id when exposed |
| `apply_url` | str? | – | canonical apply link (stored, never actioned — §2) |

**The never-invent rule (§8).** `salary` is the compensation text *as printed*; the structured
quartet is set **only** when the source exposes it as structured data. A crawler must never
parse, guess, or synthesize a number the page did not state — the host-side deterministic parser
is the single place text→number extraction happens, and it too is conservative. Likewise raw
`workplace_type`/`employment_type` are stored **as stated**; normalization into the closed
taxonomies lives host-side, never in the untrusted crawler.

**Host-side enrichment (`runtime/normalize.py`) — deterministic, LLM-free, fixable
fleet-wide.** A crawler yields raw postings; turning those into the closed taxonomies the
matcher and status surfaces reason over happens **here**, once, as pure functions. Three
reasons this is a shared host module and not per-crawler logic: (1) **no LLM in the crawl loop**
— classification must be a cheap pure function of the text, free to run on every posting every
crawl; (2) **fix once, fixed fleet-wide** — a mis-bucketed title or a new pt-BR seniority word
is fixed in *this* file and every board benefits immediately, no regeneration, no §4.11 run;
(3) **determinism = testability** — pure in→out pins the whole taxonomy under EN+pt-BR unit
tests. The enrichment (`enrich(job) -> EnrichedJob`, a frozen dataclass of `job` + `discipline`
+ `level` + `workplace` + `remote_scope` + `relocation` + `salary: SalaryRange|None`):

- **Two-axis taxonomy.** *Discipline* (what kind of engineering) and *level* (how senior /
  role-shape) are orthogonal, so the profile can say "senior-or-above backend/frontend" without
  a combinatorial enum.
  - `classify_discipline(title, description='')` → `backend | frontend | fullstack | mobile |
    devops_sre | data | ml_ai | security | qa | embedded | eng_other | non_eng`. **Title-first**
    (the cleanest signal); the description is consulted only when the title is fully opaque.
    Ordered specific-first (ml_ai before data; mobile before frontend). **Recall-safe by
    construction:** an unrecognized eng title resolves to `eng_other` (which *passes* to the
    matcher), and `non_eng` is the *only* confident drop-signal (§4.7 prefilter).
  - `classify_level(title, description='')` → `intern | junior | mid | senior | staff_plus |
    tech_lead | manager | director_plus | unspecified`. **Role-shape outranks IC seniority**
    (`Senior Engineering Manager` → `manager`; `Senior Staff Engineer` → `staff_plus`). pt-BR:
    estágio/estagiário → intern, júnior → junior, pleno → mid, sênior → senior,
    especialista/staff/principal → staff_plus, tech lead/líder técnico → tech_lead,
    coordenador/gerente/engineering manager/head of engineering → manager, diretor/vp →
    director_plus. The description fallback deliberately omits the role-shape tiers (a JD says
    "reports to the Director" about *other* roles).
- `classify_workplace(workplace_type, location, description)` → `remote | hybrid | onsite |
  unspecified`. The **structured field wins outright**; only when it is empty/unrecognized do we
  read markers (remote/remoto, hybrid/híbrido, on-site/onsite/presencial) from
  location+description, hybrid tested before remote before onsite.
- `classify_remote_scope(workplace, location)` → `anywhere | restricted | unspecified` — the
  geographic scope of a **remote** row (non-remote rows read `unspecified`). Scope-filler tokens
  are stripped from the folded location; ANY residual place token means `restricted`, an explicit
  global signal (worldwide/anywhere) means `anywhere`, else `unspecified`. Deliberately never
  reads the description (it names cities constantly and would manufacture false scopes). The
  richer per-place reading of the same string lives in §4.12.
- `classify_relocation(title, description)` → `offered | none | unspecified` — negation-aware
  phrase scan for relocation/visa support. With `remote_scope` these are the §16 "attainable"
  enums; both persist on the row and NULL-backfill at store open.
- `normalize_location(raw)` → the **surface-form** display/grouping string (`location_normalized`):
  synonyms folded ("EUA"→"United States"), separators flattened to commas, casing normalized —
  never inferring a place the text didn't name. It is the §17 role-group bucket key and a search
  LIKE target, so its output contract is frozen (tests pin it); the *structural* parse that
  preserves multi-location boundaries is §4.12's `parse_location`, a different function on the
  RAW string.
- `parse_salary(raw, description='')` → `SalaryRange(min, max, currency, period) | None` —
  **recognize-or-refuse**, versioned by `SALARY_PARSER_VERSION`. Recognizes printed notation:
  `$152,800 — $259,200 USD`, `$189K – $330K`, `R$ 8.000 - R$ 12.000`, `€60k–80k`, HTML
  entities, dollar-family prefixes (`CA$`/`A$`/`S$`… are that currency, never USD), ISO codes
  beyond the majors (CAD/AUD/SGD/PLN/CHF/INR…, an explicit code overriding a bare `$`),
  shared-magnitude shorthand (`$80-100k` = 80k–100k), and Ashby's `/ 1 HOUR` interval wording.
  A **currency anchor is mandatory** and numbers come **only from `raw`** (never mined from the
  description — a JD's "$5M in funding" can't leak in). Period defaults by currency convention
  (USD/EUR/GBP → year, BRL → month), overridden by an explicit `/hour` `/mês` `/ano` `per year`
  marker in `raw`, else a **boundary-guarded** slash marker in the description (a digit-adjacent
  `$45/hr` reads; `ERP/HRIS`, `People/HR`, and URL paths never do). **Refusals — never guesses:**
  day/week rates (no honest annualization factor), b/trillion magnitudes (funding figures),
  ambiguous symbols (`¥`, `kr`), two different ISO codes, zero/negative bounds, and periods the
  figures contradict (no major currency prints 700/hour or 150,000/month). `enrich` prefers the
  crawler's structured salary fields when set (stated period wordings map through a closed
  vocabulary; an unmappable one refuses the figure), else parses the verbatim `salary` text —
  and stamps `salary_origin` ('structured' | 'text').
- **Salary provenance + repair (§4.6).** The verbatim printed text persists as `salary_raw`
  (crawler-owned, always refreshed, out of `content_hash`) and `salary_source` records where
  the figures came from (`structured` | `text` | `llm`; NULL = pre-provenance row). A
  store-open sweep, meta-gated on `SALARY_PARSER_VERSION`, re-parses `salary_raw` under the
  current grammar and heals the measured corruption classes — zeros-as-figures, yearly figures
  mislabeled hour/month, half-k shorthand mins, sub-$10k annual bands in majors, funding-scale
  magnitudes — flipping only when the flipped reading is plausible and otherwise clearing to
  honest absence (the untouched `set_salary` NULL-guard then lets the §4.7 extract arm
  re-fill). LLM-sourced rows are never swept; `salary_raw` is never erased.
- `usd_annual_band(min, max, currency, period, *, fx_rates=None)` → `(usd_min, usd_max)`. A
  **derived comparison band**, not a claim about the posting: it converts the parsed figure to a
  common annualized-USD scale (period → year by ×2080/×12; currency → USD by a rate factor) so
  `min_salary` filters and the §16 UI can rank across currencies/periods. Persisted on the
  position as `salary_usd_min/max` alongside the untouched source fields — never overwriting the
  verbatim `salary` (the printed figure stays authoritative; this is a sort key, not a restatement).
  Returns `(None, None)` for an unknown currency, so a figure we can't convert simply doesn't rank
  rather than converting at a wrong rate. **FX rates are host-side and offline (§4.6 invariant):**
  the crawl path never fetches a rate inline. `fx_rates` is injected from `StateStore.fx_table()` —
  the static `FX_TO_USD` majors floor (conversion works before any pull, and survives a partial
  feed) overlaid with the live `fx_rates` table, which a daily out-of-band job refreshes (§4.9) and
  which broadens coverage to the feed's ~160 currencies. With no argument the function falls back to
  the static floor, keeping it a pure in→out unit under test.
- **Structured locations** — the per-place parse of the same location strings (multi-office
  lists, remote fences, worldwide claims) is its own subsystem: §4.12.

### 4.7 Dedup, position lifecycle & the matching pipeline
This section **replaces the old news-ingest at-most-once story and the `POST /news` sink
entirely** (`NewsClient`/`ingest.py` are deleted). `jobcrawler` owns a small state store
(`state.py`, SQLite for v1; Postgres is the §14 Q5 scale-up) that tracks each posting's whole
lifecycle, and drives host-side matching off the crawl's deltas.

**Content hash & the runner pipeline.** For each yielded `Job` the runner: validates it →
canonicalizes `url` (the dedup key) → applies the **completeness floor** (description length,
`min_unique_description_chars`, and an **`expired_markers` drop** — a description carrying "no
longer accepting applications" / "vaga encerrada" is dropped as `expired`, catching the
still-served-but-dead posting the enumerate-diff can't) → computes
`content_hash = sha256` over the whitespace-normalized `(title, company, location,
description)` → `store.upsert_position(...)`. The same hash formula is used by the matcher's
cache key, so a posting is scored at most once across the runner and matcher. The row persists
the **full `description` verbatim** alongside the structured fields: a position row is a
*complete* record of the posting, so the matcher can rebuild its LLM digest (and verify
`content_hash`) **from the store alone** — the save-everything property that makes profile
edits re-scorable without re-crawling (see the store-side drain below). Rows written before
this column existed backfill naturally: every re-seen position refreshes its fields on upsert.

**`positions` lifecycle (per `(site, url)` primary key).** `PositionRecord` mirrors the row.
The store owns the lifecycle columns; the crawler-supplied fields are threaded verbatim:

- `upsert_position(site, url, *, fields, content_hash) -> 'new' | 'changed' | 'unchanged'`.
  Always touches `last_seen` and resets `missing_streak`. Returns `'changed'` iff `content_hash`
  differs from the stored one (the posting's visible content moved — new title, edited
  requirements). A closed row that **reappears is REOPENED** (`status='open'`, `closed_at=None`)
  and logged as `'changed'` — a role relisted after a hiring pause is the same opening resurfacing.
- `close_missing(site, present, *, threshold) -> list[str]`. For **open** rows of the board not
  in `present`: `missing_streak += 1`; a row reaching `>= threshold` (`close_after_misses`,
  default 2) flips to `status='closed'`, `closed_at=now`, and is returned. Present rows were
  already touched by the upsert. **A single miss never closes** — a paginated board briefly
  dropping a page, or an ATS 500, is a common transient; the second confirmed absence is the
  signal. **Mass-closure guard:** the runner calls `close_missing` **only** when the board
  `enumerates_all` **and** the crawl raised no exception **and** yielded ≥ 1 valid position. A
  failed or empty run, or a partial-enumeration source, **never** advances closure — otherwise a
  transient outage would close the entire board.
- `mark_closed(site, url, *, reason)` (explicit close) and `expire_due() -> int` (close every
  open row whose `expires_at < now`) cover the two non-diff closure paths.
- Reads: `open_positions(site=None, *, limit)`, `open_urls(site, *, limit)`,
  `matches(min_score, *, limit)` (open, `match_score >= min_score`, desc), `position_counts()`
  → `{open, closed, matched, pending}` for the status surface.

For a **partial-enumeration** source (`enumerates_all: false` — RemoteOK's latest-100, a
combined RSS feed) the disappearance diff is unsound (a URL scrolls off the window without
closing), so closure there is driven **only** by the §4.9 recheck loop (re-fetch the standing
open rows and confirm they still resolve / aren't expired) and by `expire_due()`.

**The matching pipeline (the new LLM surface — `match.py`, `profile.py`).** After the runner
collects the **new/changed** positions of a crawl, it `enrich`es them (§4.6), runs the
deterministic `prefilter` (§6/§8 — drops only *definite* mismatches for free), and hands the
survivors to `Matcher.score_new(candidates)`. The matcher is the replacement for the old
per-item news POST, and every stage is built to spend as few tokens as possible while **never
dropping a job**:

1. **Cache lookup.** Key `(content_hash, profile_hash, model, PROMPT_VERSION)`. A hit is **free**
   and applied immediately (`store.set_match`). A re-crawl of an unchanged posting, a daemon
   restart, a recheck, or a pending drain all hit the cache. Editing `PROFILE.md` changes
   `profile_hash` and **lazily invalidates** every verdict — rescoring is automatic but pays only
   for jobs actually re-examined, never the whole backlog.
2. **Daily cap.** `store.match_spent(today)` counts only **LLM-scored** jobs (cache hits are
   free). Misses beyond `match_daily_cap` (default 2000/UTC-day) are `set_match_pending` — **never
   dropped** — and drained by later runs / the recheck loop.
3. **Batched scoring.** Affordable misses are scored `match_batch` (default 10) at a time in **one
   `dspy.Predict(MatchJobs)` call per batch**, under `dspy.context(lm=dspy.LM(model,
   temperature=0.0))`, on a **compact digest** per job — `{title, company, location, workplace,
   employment_type, salary-or-parsed-range, department, description[:1200]}`. The 1200-char head
   plus structured fields is deliberate token economy (the head carries most of the fit signal).
4. **Governor pacing.** `governor.pace()` before each batch smooths the calls so a big new-position
   sweep can't burst the provider into rate limits (`match_rate`, default `10/m`; orthogonal to
   the daily *volume* cap).
5. **Fail-open.** Any LLM/parse failure, or a batch-length mismatch, parks the affected jobs
   `pending` and logs — it **never raises into the crawl loop**. A matching outage degrades to
   "not scored yet". Each fresh verdict is cached (`store.cache_match`) and persisted
   (`store.set_match`, clearing the pending flag).
6. **Match-time extraction (salary + employer + geography, best-effort).** After scoring, when
   an extract model is configured (`JOBCRAWLER_EXTRACT_MODEL`, else the cheap
   `JOBCRAWLER_SUB_MODEL` — **never** the root planner), a separate
   `dspy.Predict(ExtractFromDescription)` mines postings on the cheap tier. **Three arms select
   a row** (the gate is the pass's entire cost model): (a) *salary* — matched at/above
   `extract_min_score` (default 60) with **no structured salary**; (b) *ledger tail* — its
   location string rules-parsed `partial`/`fail` (§4.12): **score-independent** (the parse is a
   property of the string, reused by every row ever carrying it) and deduped to one slot per
   distinct string per run; (c) *marker* — matched at/above `extract_min_score`, its string a
   bare workplace marker (`Remote`), and `loc_llm_at` unset. Arms (b)/(c) sit behind
   `JOBCRAWLER_EXTRACT_LOCATIONS` (default on; off reverts the gate to salary-only). The pass
   returns salary, the real **hiring company** when the board's `company` was blank or a
   placeholder, and `location_geo` — the geography the posting *states* (ISO2-coded places,
   remote `none|fenced|worldwide`, the fence), vocabulary-validated against the §4.12 gazetteer
   (junk dropped, never coerced; never inferred from HQ, currency, or language). It is
   separately cached on `(content_hash, model, EXTRACT_VERSION)` — **no `profile_hash`**,
   because salary, employer, and geography are properties of the posting, not the profile — and
   a cache hit re-applies for free on every run (which also re-heals the §16 dedup key a crawl
   upsert reverted). Fresh (cache-miss) extractions are bounded to `extract_cap` (default 50)
   per run so a first pass over a big backlog can't spike spend; the overflow **re-parks**
   (`set_match_pending`) so the drain re-presents those rows next tick — the verdict re-applies
   from cache for free and extraction takes a fresh slot. Long descriptions send **head (2000)
   + tail (4000)** chars — pay lives in footers: on the measured gate pool, 0/88 stated ranges
   sat inside a head-only window and 88/88 inside head+tail (`extract-v3`). It is strictly
   additive and conservative: `set_salary` only
   fills a row that has **none** (a source salary always wins and is never clobbered — this is
   why `_position_fields` **omits** the salary columns on a salary-less crawl rather than sending
   NULLs), defaults a model-omitted period by the §4.6 currency convention (so LLM fills never
   mint permanently-NULL USD bands), and stamps `salary_source = 'llm'`; the extractor returns
   `null` unless the description *literally* states pay **with a
   currency** (never inferring from seniority/market/location) and refuses whole figures under
   a stated period outside `year|month|hour` or a non-ISO currency; `set_hiring_company` records
   the cleaner name and repoints the §16 `company_canonical` dedup key at the real employer,
   and the geo answer routes by string class — tail strings fill the §4.12 value ledger,
   marker rows get posting-local fence rows, both first-writer-wins, and `loc_llm_at` stamps
   even an empty answer so the marker arm never re-selects the same content. Nothing here is in
   `content_hash`, so backfilling never re-dedups a posting or invalidates its verdict. Like
   scoring, it is **fail-open** — a bad extract batch is skipped, never raised into the crawl
   loop, and never affects the verdict the runner notifies on.

**First-crawl cap.** On a board's first-ever position-yielding crawl only the newest
`first_crawl_cap` (by `posted_at`, discovery order as fallback) go to the matcher now; the tail is
`set_match_pending`. A brand-new board can list hundreds of standing roles — without this a first
crawl would burn the whole daily budget scoring stale backlog instead of fresh postings.

**Notify.** The runner filters the applied verdicts by `match_min_score` (default 70) and calls
`notify('match', …)` for the strong ones (§4.9 notify module: an INFO log line, plus an optional
webhook POST). The `Matcher` itself is **threshold-agnostic** — it scores everything and stores
the verdict; the notify boundary is the runner's, keeping the decision threshold off the matcher
surface (§8). `pending_matches(*, limit)` and `matches(...)` back the drain and the operator's
match view.

**Freshness decay (the effective score).** The stored `match_score` is the matcher's raw *fit*;
what the operator ranks, filters, and sees is an **effective score** = `match_score` × a recency
multiplier in `[floor, 1]` that decays with the posting's age (from `posted_at`, else
`first_seen`), computed in SQL at read time (never stored — age changes continuously). So a newer
posting outranks an equally-good older one, while the **floor** (`JOBCRAWLER_SCORE_FRESHNESS_FLOOR`,
default 0.6) keeps a great fit represented — a base-90 fit never falls below 54, so it stays in the
browse even after it drops out of a high-`min_score` cut. Half-life is `JOBCRAWLER_SCORE_HALF_LIFE_DAYS`
(default 30; `floor = 1.0` disables decay). It is applied **everywhere the score surfaces** — `matches()`
ordering + `min_score` threshold, `search_positions()` `sort='score'` + `min_score`, the dashboard
`score_histogram` bands and "top matches", and the list/detail/CLI display (rounded, with the raw fit
shown alongside on the detail page). The §4.9 **notify** gate stays on the raw verdict: a match is
scored at discovery when age ≈ 0, so decay is a no-op there — decay is a browse/ranking lens, not a
re-litigation of whether the fresh find was worth a ping.

**Profile edits & the store-side drain.** Crawling and matching are fully decoupled: every
position is persisted at crawl time regardless of any verdict, and rescoring works from the
store. The persisted `meta.profile_hash` records the hash verdicts were last applied under.
The daemon's **match-drain tick** (`JOBCRAWLER_MATCH_DRAIN_INTERVAL`, default 10m, `off` to
disable; autonomy-EXEMPT like the recheck — judgement over gathered data, not
self-modification) runs a three-step pass, first firing **on boot** so an edit made while the
daemon was down is caught immediately:

1. **Profile watch.** Reload `PROFILE.md` from disk; a hash change (or a profile/model that
   arrived after boot) rebuilds the shared matcher in place, so subsequent crawls score under
   the new profile without a restart. A *deleted* profile disables matching; a momentarily
   *malformed* one (mid-edit save) keeps the old matcher and retries next pass.
2. **Requeue on edit.** When the disk hash differs from `meta.profile_hash`,
   `requeue_open_matches()` parks **every open row** (scored, prefilter-skipped, or never
   presented) and the meta is updated. Old-profile verdicts on *closed* rows are history and
   stay untouched. First-ever adoption of the meta key records the hash without a requeue.
3. **Drain.** Up to `JOBCRAWLER_MATCH_DRAIN_CAP` (default 200) parked rows per pass are rebuilt
   from their stored fields (`drain_candidates`): a row **without** a stored description (or
   whose rebuilt job doesn't hash back to its own `content_hash`) is *unscorable host-side* and
   stays parked for its next crawl to backfill; a row the deterministic prefilter now
   definitely rejects is **unparked verdict-less** (`clear_match_pending` — back to "tracked,
   unscored", so permanent mismatches can't clog the FIFO); survivors go through the ordinary
   `score_new` path — cache hits free, fail-open parking, strong verdicts notified exactly
   like the runner's sink. The drain scores against the **full** daily cap while fresh
   crawl-time scoring holds `JOBCRAWLER_MATCH_DRAIN_FLOOR` (default 160) of that cap back: a
   day of saturating new inflow can otherwise reserve the whole cap and starve the drain to
   `granted=0` every tick, so the parked backlog never shrinks. Reserving a floor guarantees
   the drain a slice; it still gets anything fresh scoring left unused (the floor is a
   guarantee, not a ceiling). Unchanged `(job, profile)` pairs re-apply from cache for **zero
   tokens**, so a requeue of thousands of rows only pays for verdicts the edit actually
   changed.

`jobcrawl rematch` runs the same logic on demand (free cache passes over parked + open scored
rows, then one capped LLM drain from the store) and refreshes `meta.profile_hash`, so the
daemon doesn't requeue a second time for an edit the operator already rematched by hand.

**Operator tracking axis (host-side policy, §16).** Orthogonal to the crawler's `open`/`closed`
lifecycle above, the store carries a second, purely operator-owned axis for the §16 tracker:
`track_status` (a personal pipeline — `new → interested → applied → interviewing → offer` with
terminal `accepted`/`rejected`/`withdrawn`), a `dismissed` flag, free-text `notes`, and an
`applied_at` date stamped once when a status first implies an application was sent. These columns are
**not** in the crawler-supplied `fields` set, so an upsert or reopen (above) never clobbers them — a
posting you marked `applied` that the board edits stays `applied`. Per §4.7's own division of labour
the store persists `track_status` as an **opaque string** and stamps `applied_at` only when told to;
the valid set, transitions, and the "which stage implies applied" policy live host-side in
`tracking.py` (the store records state, never invents policy). This axis is a pure operator surface:
like the match `reasons` (§8), no generator/optimizer code path ever reads it.

**Hiring-company dedup (§16).** Beyond the board it was found on, each position carries the hiring
`company` and a derived `company_canonical` grouping key — the company folded to lowercase, stripped
of a leading "the" and trailing legal-entity suffixes (Inc/LLC/Ltd/GmbH/…) and punctuation, so
"Acme", "Acme, Inc." and "The Acme Company" collapse to one key (`canonical_company`, §4.6). It is
recomputed host-side on every upsert and kept **out** of `content_hash` (which hashes the verbatim
`company`), so refreshing it never forces a re-dedup or a match-cache miss. It backs the top-matches
rule "show a company's roles only until I've applied to one of them": `matches(exclude_company_applied=…)`
drops every open row whose canonical company already has an `applied_at` stamp — most employers accept
one application company-wide, so a second lead at a company you're already in is noise. The dashboard
shortlist leans on the same key from the other side: at most one card per company — its best-effective-fit
row — since eight slots read further when each names a different employer (every role stays in
`/positions`; a blank key never groups). Grouping
COALESCEs the key to `lower(company)` for rows written before the column existed (they converge on
their next crawl), and an empty key groups with nothing. Company grouping is orthogonal to the §17
*job-identity* overlay: this folds a company's many roles; §17 folds one role's many board copies.

### 4.8 Health & self-healing regeneration
Each crawl records `positions_found`, `new_positions`, `errors`, `pages_fetched`, and a
`description_quality` signal per board. A board is flagged **stale** on a *single* zero-**`positions_found`**
run (the **blackout** signal — **never** zero `new_positions`, since a healthy board legitimately
adds nothing for days) against a baseline that previously yielded positions, confirmed by a short
re-probe that **re-parses ≥ 1 complete position** (not merely network-OK). One re-probe-cleared blip
is tolerated. A separate **partial-drift** signal fires when `description_quality` (the share of
fetched positions whose description cleared the completeness bar, §4.6) collapses below
`description_quality_floor` for the latest two runs while position counts stay healthy — a board that
started serving title-only stubs or truncated postings. Both signals are host-side, never surfaced to
the generator (§8). Stale boards are re-entered into §4.2 generation; the new script is validated and,
per the review policy (§8), staged for approval or auto-promoted (with a versioned backup for
rollback). This is the "regenerate when the board changes" loop made automatic.

Regeneration runs the **current** generator. If generation exhausts its budget without a
gate-passing crawler — including a forced RLM extraction (`final_reasoning == "Extract forced final
output"`, §4.2) — the board enters a distinct **generation-failed** state (held with a geometric
backoff, then re-tried with the — §4.11-improved — current generator), surfaced separately in
`jobcrawl list`/`status` and *not* silently re-enqueued into the same failing generation (which
would spin). The backoff alone never gives up — at its cap it would re-generate a chronically dead
board (a retired URL, an un-crawlable rewrite) forever — so after `JOBCRAWLER_GENFAIL_MAX_ATTEMPTS`
consecutive failures (default 8, `0` disables) the board is **parked**: held generation-failed and
no longer auto-re-generated, bounding the spend. It stays surfaced and un-parks the moment it heals
on its own or an operator regenerates it by hand. A regeneration that **crashes** (the entrypoint refusing the corpus prefetch, LM
transport, sandbox runtime) lands in the same held state: an escaping exception writes no health
record, so no backoff would engage and the site would re-enter the identical crash every tick —
the hot loop this hold exists to prevent. Relatedly, the corpus prefetch itself must **degrade,
not die**, when the registry URL refuses a bare GET (an API index wanting auth headers, a bot
wall): the refusal is recorded in-band in the corpus as a fact for the generator — the operator
hints carry the recipe for exactly such boards, and the §6 gate still proves the result live.

**Generation log (durable "why", §4.2).** Because generation is **LLM-gated** — whether a candidate is
produced, and why a fan-out came up empty, is decided by what the model was asked and returned — each
attempt records a row to the `generation_log` store: its `outcome` (`promoted`/`staged`/`failed`), the
top-level `reason` (a §6 gate reason, `"no candidate produced"`, or an escaping exception), the winner's
`score`, and — the substance — the captured **LLM interaction log** (each call's prompt/completion, or
the error that aborted it). The daemon's stdout dies with its terminal, so this is what makes a
post-mortem answerable after the fact (the trigger was a fleet-wide generation failure whose cause
lived only in a since-closed terminal). Capture is a per-attempt, thread-scoped dspy callback on the
generation LMs (so concurrent self-heals don't cross-contaminate) and is best-effort — recording never
breaks a crawl. Rows are bounded by a retain cap; read back with `jobcrawl generation-log [site] [-v]`.

Track **drift-MTTR** — median time from a previously-healthy board going dark to a gate-passing
replacement being *enabled* (detection + regeneration + review gate) — as an **observability metric**
(`jobcrawl status`), preferring the durable health-transition log and falling back to run-history
reconstruction; the §14 Q2 review-gate tier trades directly against it (more human review lengthens
recovery). It is *not* a milestone exit gate (gating a close on a median-over-drift-events would need
enough organic drift events to exist first — a bootstrapping circularity).

### 4.9 Scheduler / orchestrator (owns the run loop)
`jobcrawler` runs as a **long-running service** — `jobcrawl run` starts an async scheduler that
operates the whole loop itself rather than relying on an external cron.

- **Due-selection.** Each board has a cadence (`schedule` in §4.1, else `JOBCRAWLER_DEFAULT_SCHEDULE`,
  default **2h** — job boards move on the order of days, not minutes). The scheduler keeps `next_run`
  per board in the store and dispatches those whose time has come.
- **Adaptive cadence.** A board flagged `adaptive` (the fleet default via `JOBCRAWLER_ADAPTIVE_DEFAULT`)
  learns its own interval from recent hit/miss outcomes (an AIMD hit-rate controller steering toward
  ~`1/target_n` postings-per-check), bounded to the politeness band `[min_schedule 1h, max_schedule
  3d]`. Adaptive cadence is **timing, not code**, and stays within the politeness band, so it is
  **exempt from the §4.8/§4.11 autonomy brake** (which freezes only self-*modification*).
- **NEW — recheck / expiry timer.** On `JOBCRAWLER_RECHECK_INTERVAL` (default 12h) the scheduler
  re-fetches up to `recheck_cap` (default 300, oldest-checked first) standing **open** positions to
  confirm they are still live. This is the **only** closure signal for `enumerates_all: false` sources
  (where the diff is unsound), and it also re-verifies enumerating rows against `expired_markers` /
  `expires_at` and calls `expire_due()`. The recheck GET follows **same-host** redirects (bounded,
  each hop SSRF-rechecked, robots-gated and paced — boards commonly locale-redirect every posting
  URL, which would otherwise blind the sweep to the real 200/404 behind the 3xx); a **cross-host**
  redirect is a moved posting, not closure evidence, so the 3xx is surfaced and the row stays open.
  Like adaptive cadence it is **observation, not
  self-modification** — it neither rewrites a crawler nor swaps the generator — so it too is exempt
  from the autonomy brake.
- **NEW — FX-rate refresh timer.** On `JOBCRAWLER_FX_REFRESH_INTERVAL` (default 24h) the scheduler
  pulls live currency→USD rates from `JOBCRAWLER_FX_ENDPOINT` (a keyless USD-based feed) and writes
  them to the `fx_rates` table for the §4.6 `usd_annual_band` conversion. This keeps the §4.6
  pure/offline invariant intact: the rates are fetched **out-of-band** here (a plain host-side GET,
  not through the crawl `ctx` SSRF path — same posture as `notify.py`), never inline on the crawl
  path. It is **observation, not self-modification** (published exchange rates feeding a sort key), so
  like the recheck it is **exempt from the autonomy brake**. Fail-soft: a failed pull is logged and
  the last stored rates stand (the feed moves once a day and coarse ranking tolerates the drift); the
  timer advances on every attempt so a flaky endpoint waits for the next window instead of retrying
  each tick. `jobcrawl fx-refresh` forces a pull on demand (and surfaces errors, unlike the daemon).
- **Bounded concurrency.** A global cap (`JOBCRAWLER_CONCURRENCY`) limits how many boards crawl at
  once; per-host limits come from §4.5. A board never overlaps its own prior run.
- **Cold-start bootstrap is budgeted per tick.** A due site with no committed crawler is never
  crawled (it would only `FileNotFoundError`); the tick routes it to the §4.2 bootstrap **after** the
  crawl wave. That pass carries a time budget (`JOBCRAWLER_BOOTSTRAP_BUDGET`, default two tick
  intervals): one generation blocks the single-process loop for many minutes, and an unbudgeted pass
  over a cold roster runs every generation back-to-back inside a single tick — while each freshly
  promoted crawler sits idle, due but never dispatched, until the whole wave ends. Over budget, the
  rest of the roster defers to the next tick, where crawls (including the just-promoted crawler's
  first) always run before more bootstrapping. Cheap assesses (a site in generation-failed backoff)
  don't consume the budget in any meaningful way, so a no-op sweep still covers the roster in one
  tick; deferral loses nothing because crawler-less sites are re-detected from disk every tick.
  Within a pass, generations run in bounded-concurrency waves (`JOBCRAWLER_BOOTSTRAP_CONCURRENCY`,
  default 6) so a fresh roster comes up in parallel instead of one multi-minute generation at a time,
  while still capping simultaneous LM spend/rate; the time budget is checked at each wave boundary and
  forced "crawl now" slugs run first, exempt from it.
- **Retry & backoff (one owner per failure class).** A crawl that fails *before yielding any position*
  gets a small bounded backoff+jitter retry **here**; **transient fetch** errors belong to §4.5;
  **matching** failures are §4.7's fail-open-to-pending and are *not* scheduler-retried. Persistent
  failure feeds the §4.8 health signal instead of stalling the queue.
- **Regeneration & re-optimization triggers.** A timer (`JOBCRAWLER_REGEN_CHECK`) evaluates staleness
  and enqueues regeneration (§4.8); a slow timer (`JOBCRAWLER_REOPT_CHECK`) or a trainset-growth
  threshold **hands off** the offline §4.11 job out-of-band (the scheduler never runs `.compile()`
  in-band — a multi-hour GEPA run would violate the never-stall guarantee). The staleness sweep
  covers only sites **with** a live crawler: a crawler-less site is the budgeted bootstrap's job
  (above), and since the healer treats a missing crawler as an immediately-confirmed regenerate, a
  sweep that included those sites would re-run the very generations the bootstrap budget deferred —
  unpaced, and right at daemon boot, where the first sweep is always due. One generation owner per
  site state: bootstrap owns cold-start, the sweep owns drift of the committed fleet. Both self-modifying
  triggers, plus auto-promotion and auto-adoption, are gated by the master **autonomy** brake
  (`JOBCRAWLER_AUTONOMY`, default on): `off` freezes all self-modification while the daemon keeps
  crawling, matching, and recording signals.
- **Run history.** Each run records the canonical Run record (§5) for observability and the staleness
  math. **Crash-safety:** a prior daemon killed mid-crawl leaves `finished=NULL` rows that would look
  perpetually in-flight; the run loop reaps those orphans at boot (single-process, nothing is yet
  in-flight).
- **Lifecycle.** Graceful shutdown drains in-flight crawls; state lives in the store, so the loop is
  crash-safe and restart-idempotent.

v1 uses an **in-process async scheduler** (no external broker) — appropriate for a checked-in board
list; a task queue is a later scale-up (§14). The optional thin HTTP surface (status, manual trigger)
this reserved is realized by the in-process **§16 web tracker** `jobcrawl run` mounts — a read/write UI over the
same store; it never runs a scheduler or an LLM, only flagging crawl/rescore work for this loop to drain.

### 4.10 Cloudflare / anti-bot challenge handling
Some job boards sit behind Cloudflare, but most use it only as a CDN (plain `httpx` works), and
**API/feed-first discovery** (§4.2) often sidesteps challenges since ATS JSON endpoints and RSS feeds
are frequently unprotected — but *how* often is a **measured fact**, not an assumption (the M1 step-0
tally, §13). When a real challenge appears, `ctx` handles it **transparently** — generated scripts
never contain anti-bot logic.

- **Detection.** A response is flagged on known challenge signals (`403/503` with `cf-mitigated:
  challenge`, or `Server: cloudflare` plus interstitial/Turnstile markers).
- **Escalation ladder.** `ctx.fetch` escalates *only on detection*: **static httpx → headless browser
  → challenge solver**, bounded by the board's `render` ceiling (§4.1). No challenge → no escalation.
- **Plain Playwright/Selenium is not enough.** Vanilla automation is fingerprinted and fails CF's JS
  challenge, so the solver tier uses a **stealth** browser.
- **`ChallengeSolver` interface (pluggable).** `solve(url) -> {cookies, user_agent}`. Backends: an
  **in-process stealth browser** (nodriver / camoufox / patchright — *recommended primary*);
  **FlareSolverr** (an HTTP service, least code to adopt, historically lags CF updates); a **paid
  CAPTCHA solver** for interactive Turnstile (optional, off by default — cost + ToS; see non-goals).
  Default backend is `none` (plain httpx) until the stealth tier is built.
- **Clearance cache ("pay once").** A solved `cf_clearance` + its User-Agent are cached per host and
  replayed with cheap `httpx` until expiry. **Constraint:** `cf_clearance` is bound to **IP + UA**, so
  the solver and the subsequent cheap requests must share egress IP, User-Agent, and proxy.
- **Budget & give-up.** The solver tier is slow and concurrency-capped; a board needing an interactive
  CAPTCHA on every request is marked **unsupported** rather than retried forever.

### 4.11 Self-improving generator (offline optimization loop)
The generator is **not static** — it improves over time by optimizing the instruction it generates
from. This is a **core capability**, but deliberately **offline, batched, and out-of-band**: it never
runs on the crawl path or inside the §4.9 scheduler's in-band work. Two loops, never conflated:

- **Online regeneration (§4.8)** invokes the *current, pinned* generator and stays in the scheduler.
- **Offline self-improvement (here)** runs an optimizer over accumulated examples and produces a
  *new, versioned* generator program for the regeneration loop to adopt.

**The generator is a `dspy.Module`.** Wrap the `dspy.RLM` (and the §6 selection loop) in
`CrawlerGenerator(dspy.Module)`; sub-modules held as attributes auto-register as parameters, making
them visible to optimizers and to `save`/`load`. **Async caveat:** `BestOfN`/`Refine` implement only
sync `forward`, and `Module.acall` awaits `aforward` with no sync fallback — so
`CrawlerGenerator.aforward` must invoke the wrapper's **sync** surface offloaded via
`asyncify`/`run_in_executor`, never delegate into a missing wrapper `aforward` (which raises
`AttributeError` and/or blocks the single-process scheduler). RLM stays primary; we do **not** invert
to CodeAct.

**What GEPA optimizes.** RLM *does* expose optimizer-targetable predictors (`generate_action`,
`extract`), but optimizing them mutates RLM's **generic action loop** — a broad, model-sensitive blast
radius that collides with the §8 immutable TCB. So by **design choice** GEPA is scoped to a *narrow*
predictor we own — an extracted `dspy.ChainOfThought` *strategy* sub-module (`site_features ->
discovery_plan`) the RLM/CodeAct loop executes — for blast-radius control [Darwin-Gödel-Machine]. GEPA
rewrites that predictor's docstring-instruction. `dspy.CodeAct` is the fallback if it optimizes
unevenly.

Two load-bearing requirements on this predictor. **Seed it** with a hand-authored, *ordered-rule*
docstring enumerating the discovery decisions — now **posting-domain**: *probe for a public ATS API
(Greenhouse/Lever/Ashby/Gupy token) before any DOM scraping; API > feed > sitemap-freshness >
HTML-heuristic; reconstruct the full description per backend (Lever concatenation, Greenhouse
unescape); verify ≥ N postings and a `meta.total`-consistent count before finalizing* — optimizers
can't infer a rule set from a vacuous seed [MIPRO]. And define `site_features` (the predictor's
**input**) to carry the per-board facts GEPA must *not* memorize (discovered API/feed URLs, board
outline, `meta.total`), forcing it to evolve *generalizable* strategy rather than hard-coding fixture
tokens — GEPA's meta-prompt deliberately bakes domain facts into the instruction, so without this it
overfits the trainset's board mix and **destroys the cold-start payoff** [GEPA, gskill].

**One scoring core, three adapters.** §6's **gates 1–2** are computed by a single pure scoring core:
one `validate_crawler` run → `CandidateResult` + a graded float score + a `gate_failures` string. It
runs the candidate through `ctx`; the **offline** metric injects a replay-backed `ctx` (persisted
`corpus` + recorded HTTP) so it never hits the network. The **gate-3** stability verdict is computed by
the harness comparing *two* core invocations and **appended to the GEPA feedback text**, never
entering the scalar. Adapters sit over the core — the **GEPA 5-arg metric and the `dspy.Evaluate`
metric are signature-compatible** (one callable serves both), so expose them as *one* example-based
metric; only `BestOfN`'s `reward_fn(kwargs, pred) -> float` is structurally different.

| consumer | signature | note |
|---|---|---|
| `BestOfN`/`Refine` (§6) — **the outlier** | `reward_fn(kwargs, pred) -> float` | 1st arg is the module **kwargs dict**, returns a bare float |
| `dspy.Evaluate` | `metric(example, pred)` | Evaluate passes **no** `trace` |
| **GEPA** (same callable) | `metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> Prediction(score, feedback)` | optimizers pass `trace` |

GEPA's **per-gate textual feedback** ("AST gate: banned import `os`", "0 postings: Lever `description`
read alone — reconstruct with `lists[].content`", "unstable across two runs") — not the scalar — is
the whole reason it is chosen. The reward_fn also reads `pred.trajectory` / `pred.final_reasoning`,
lowering the score on a forced RLM extraction. The noisy stability gate stays a hard accept/reject and
is **excluded** from the scalar to keep selection low-variance.

**Feedback content (the actual signal).** The GEPA/`gate_failures` text must be **un-summarized and
concrete** — not "0 postings" but "0 postings: `discovery_url` /api returned the intro-only
`description`; full text needs `+ lists[].content + additional` (Lever)". **Never** LLM-summarize
`CandidateResult` before handing it to `reflection_lm` (full traces scored 50.0 vs scores-only 34.6;
*summaries were no better than the scalar* [Meta-Harness]). Order by **severity** and **cap the
foreground to the single most-blocking failure** (many co-equal directives degrade compliance
[TextGrad, JudgeFlow]). Attribute by **counterfactual** — phrase the failure as the upstream
`discovery_plan` decision that, *if changed, clears the downstream gate* — the only thing GEPA can
change [JudgeFlow] — and include the **delta**. Mis-attributed feedback scores *below none*
[Feedback-Descent]. Additionally feed **across-run aggregates**: condense the §4.9 run-history +
§4.8 staleness per accepted board into per-board failure summaries and thread them in — patterns
invisible in any single trace surface only in aggregate [HALO].

**Trainset.** `list[dspy.Example]`, one per accepted generation, mirroring the `GenerateCrawler`
inputs, `.with_inputs(...)`. It is **inputs-only / reference-free** — the metric scores by *running a
fresh crawler*, so the recorded `discovery_*`/`crawler_source` are provenance, not labels. **Admission
rule:** admit a `(site, corpus)` pair **iff** §6 **gates 1–3** pass, the §8 adversarial
cross-validation passes, and the board stayed non-stale (§4.8) for `JOBCRAWLER_TRAINSET_STABLE_RUNS`
(~3) runs. §6 **gate-4** (human approval) is **not** a precondition — it gates *enabling a script on
the crawl path*, a separate concern; the §4.11 loop is offline and automatic. The frozen `corpus` dict
is persisted with each example (plain JSON, JSONL trainset). A **confirmed-failed board** is also
captured as a §4.11 *hard case* (site identity + render + hints; the corpus is re-prefetched fresh at
optimize time so the strategy is scored against the board's *current, drifted* state) — a genuinely
broken board feeds the optimizer's difficulty signal before self-healing tries to fix it.

**Saturation is the headline failure mode.** Because admission keeps only gate-passing, non-stale
crawlers, every example is near-success — and a near-binary reward then makes **GEPA no-op** (its
reflection LM never fires when every sampled minibatch scores all-correct) [GEPA-+25pts]. Mitigate:
(i) graded scalar (§6); (ii) `optimize.py` asserts a **minimum fraction of sub-maximal scores in the
sampled minibatches** and on `baseline ≈ optimized` emits the three-way remedy (re-bias/harden the
trainset, run on a *cheaper* optimizer executor to manufacture failure signal, or accept saturation
and defer); (iii) **bias** GEPA's train/val toward *hard / discriminative* cases (recently-drifted,
many-retry, barely-passing) — difficulty, not count, supplies signal [Meta-Harness, HALO]. Also assert
**per-instance spread**: GEPA's Pareto sampling consumes per-board score *variance* [GEPA, TRIPLE].

**Optimizer ladder.** Escalate by trainset size, not straight to GEPA:
- *No trainset* → run no optimizer; keep §6's `BestOfN`/`Refine`.
- *~5–20 pairs* → **`BootstrapFewShotWithRandomSearch`** over the owned predictor (a pair's value =
  held-out transfer of including it as a demo), with a diversity step and the graded scalar as utility
  [CodeAdapt, TRIPLE].
- *feedback-rich regime* → **GEPA** (instruction evolution), justified by *feedback-richness*, not low
  data.
- *~200+ pairs* → **`MIPROv2` joint instruction+demo** — *documented but not built in v1* (200+
  accepted pairs is multi-month-to-never for a near-static list).

**Three disjoint pools — assert it.** GEPA takes *separate* caller-supplied `trainset` (reflective
minibatches) and `valset` (candidate-selection / Pareto); if `valset` is omitted it **reuses
`trainset`** (`valset = valset or trainset`) — not guaranteed disjoint, which is why we pass a distinct
`valset`. The **promotion held-out** is a *third*, frozen pool GEPA never sees. Assert (and test) zero
overlap across train / val / promotion.

**GEPA config.** 5-arg feedback metric; `reflection_lm` = strong model
(`JOBCRAWLER_REFLECTION_MODEL`), ideally a different family than the planner; exactly one budget
(`auto="light"` to start); `compile(..., teacher=None)`; `track_stats=True`. **Validation dominates
the budget**, so keep `valset` small; keep `minibatch≈3`; **retain Pareto sampling**; leave **Merge
OFF** for v1 [GEPA, GEPA-+25pts].

**Promotion gate (regression-safe).** Evaluate candidate **and incumbent** on the *same* frozen
fixtures, offline, averaging `promotion_eval_passes` (default 3) since each gate run spins a fresh
non-deterministic RLM sandbox. Promote only if the candidate beats the incumbent by a **margin
exceeding eval noise**. **Co-gate on an independent true-quality signal:** the candidate must *also*
beat the incumbent on the §8 adversarial cross-validation pass-rate — promote only if **both** improve;
if the §6 proxy rises while adversarial quality is flat/down, **reject as over-optimization** [HALO,
DGM, GEPA-+25pts].

**Trigger & persistence.** Out-of-band — manual, a slow `JOBCRAWLER_REOPT_CHECK` cadence, and/or a
trainset-growth threshold. The offline job runs **detached** under a single-flight lock (a reopt tick
firing during a running compile is a no-op). The optimized program persists as **JSON state**
(`generator.save(..., save_program=False)`) — **never** cloudpickle (`.json` suffix is what makes it
safe; `save_program=True` would be arbitrary-code-exec on load, contrary to §8). A single config
pointer (`JOBCRAWLER_GENERATOR_PATH`) selects the active versioned artifact; flipping it to a prior
version is the **rollback** (`jobcrawl rollback-generator`). Under "full-auto, gated by proof"
(`--adopt`) the daemon adopts a generator the instant it clears the frozen held-out margin, then a
**probation watcher** (`JOBCRAWLER_ADOPT_PROBATION`, default 48h) compares post-adoption
generation-failures against the equal window before and **auto-rolls back** on a regression of
`adopt_regression_delta` (default 2) distinct boards — because the held-out is a proxy and a generator
can pass it yet regress on the live fleet.

**Honest payoff.** This does not make "every board better over time" — the input is a near-static
list. It optimizes for (a) **regeneration quality under drift** (§4.8) and (b) **cold-start on
newly-added/unseen boards**, validated on the held-out split.

### 4.12 Structured locations (deterministic-first)

Location strings arrive in ~3k surface forms per 15k rows — `USA` / `United States` /
`Remote - US` / `US - San Francisco`, semicolon/pipe/bullet multi-office lists, macro-regions
(`Home based - EMEA`), worldwide claims — and §4.6's `location_normalized` deliberately only
cleans the *surface* (it folds every list separator into commas, destroying the boundaries).
This subsystem gives every position **structured places and remote-eligibility**, rules-first,
with the §4.7 LLM pass reserved for the residue the rules cannot read.

**The parser (`runtime/geo.py`).** `parse_location(raw) -> LocationParse` — pure, no I/O, no
LLM, gazetteer-in-code (countries+aliases, US states / CA provinces, macro-regions, a famous-city
table that is empirically decisive: without it clean coverage drops 97%→81% of rows). Output:
ordered `places` (city / subdivision / ISO2 country / macro), a remote `fence` (city-less
geography attached to a remote marker: `Remote - US` fences to `US`, never becomes an office),
`remote_marker` / `worldwide` flags, and a `quality` in `full | macro | marker | noise | partial
| fail`. Input is the RAW `positions.location` (or one `locations_json` element) — never
`location_normalized`. The tokenizer guards are load-bearing (ALL-CAPS-only short aliases so
prose "at"/"per" never anchor; place-shaped absorption checks; `Remote-first` never splits) and
the corpus is pinned by tests. Any behavior change **must bump `GEO_PARSER_VERSION`**.

**Storage — three additive tables + three columns.** `location_values` is the parse **ledger**,
keyed `(site, value)`: `parse_source='rules'` rows are disposable (reparsed whenever
`GEO_PARSER_VERSION` moves); `'llm'` rows are durable facts paid once via §4.7 and reused by
every posting ever carrying that string — retired only when an upgraded rules parse comes back
clean (deterministic-first). `position_locations` is the per-position overlay in the §17
disposable-projection style: ordered rows `(kind ∈ place | remote_fence | remote_anywhere,
city, subdivision, country, macro_region, display, source, confidence)`; `source ∈ rules |
llm_value` rows re-derive from positions × ledger at any time, `llm_posting` rows are
posting-local facts (description-mined fences) that survive resweeps and purge when the
posting's content changes. `geo_region_members` materializes `GEO_REGION_MEMBERS` for SQL
(macro-expanded filters; reseeded whole under `GEO_REGION_SEED_VERSION`). On `positions`:
`locations_json` (crawler-owned raw array — Greenhouse `offices[]`, Lever `allLocations`,
Ashby `secondaryLocations` — refreshed every crawl), `location_display` (the store-owned
canonical human line, full English country names; NULL falls back to
`location_normalized`/`location` everywhere), and `loc_llm_at` (the §4.7 answer stamp).

**Write path.** The runner calls `refresh_position_locations` right after every upsert with
`force = (outcome != 'unchanged')` and `purge_llm = (outcome == 'changed')`; the unchanged path
costs one ledger SELECT. A country-only string on a `workplace='remote'` row projects as a
`remote_fence` (the same reading `classify_remote_scope` gives the flat string, so the
structured rows never contradict the §4.6 enums); `worldwide` projects `remote_anywhere`; bare
markers project nothing (display NULL → raw fallback). A store-open sweep (meta-guarded by
`geo_parser_version`) reparses every stored string and rebuilds every row — closed included —
once per parser version.

**Read path.** Display surfaces render `location_display`-first (list rows, role-group picker
labels, detail header + Locations panel, API `location_display`/`locations` keys). Search gains
one LIKE over `location_display` (the synonym bridge: `q=brazil` finds a raw `Brasil` row) and
two structured filters: `country=` (any resolved office or fence in that ISO2) and `remote_in=`
(remotely workable from there: fence match, macro fences expanded through `geo_region_members`,
`remote_anywhere` matches everything; office places never do), plus `exclude_country=` — the
mirror drop, which never fires on unresolved rows (powering the §16 "Relocation (non-US)"
checkbox: relocation offered and no resolved US office/fence).

**Boundaries (what it must not do).** Never write `location`, `location_normalized`,
`workplace`, `remote_scope`, or `relocation` (their writers are §4.6's, frozen). Never enter
`content_hash` (§4.7) — parses re-derive freely without re-hashing, re-deduping, or re-billing.
Never feed the MatchJobs digest (its `location` input stays the RAW string; `PROMPT_VERSION`
untouched). §17 stays frozen: role-group buckets and the T5 gate keep reading
`location_normalized`-else-raw — `location_display` is a label there, never a key (re-keying is
§14 Q10, an explicit sign-off). The §16 attainable gate keeps reading the §4.6 enums only.

## 5. Data & interface contracts (summary)

- **Site entry** — §4.1 (adds `company`, `enumerates_all`).
- **Generated module** — `SITE: str`, `crawl(ctx) -> Iterable[Job]`; allowlisted imports only (§4.3).
- **`ctx`** — `fetch`, `render`, `get`, `log`; enforces robots/rate-limit/timeouts/allowed-hosts.
- **`Job`** — §4.6; required `url/title/company/location/description/source`, optional
  salary/workplace/employment/timestamp/id fields; **no `Item` alias** (clean break).
- **`EnrichedJob`** — `Job` + `discipline` + `level` + `workplace` + `remote_scope` +
  `relocation` + `salary: SalaryRange|None` (host-side, §4.6).
- **`PositionRecord`** — the `positions` row (§4.7): identity + the crawler fields + the §4.6
  derived taxonomy (`discipline`/`level`/`workplace`/`remote_scope`/`relocation`,
  `location_normalized`, `salary_usd_min/max`) + lifecycle
  (`first_seen`/`last_seen`/`missing_streak`/`status`/`closed_at`) + match (`match_score`/
  `match_verdict`/`match_reasons`/`matched_at`/`match_pending`) + the §4.7-mined
  `hiring_company` and the §16 `company_canonical` dedup key (both **out** of `content_hash`)
  + the §17 verdict-provenance `match_origin` (`'llm'` \| `'cache'` \| `'cluster:<site>\n<url>'`;
  NULL on legacy rows) + the §4.12 trio (`locations_json` crawler-owned, `location_display` and
  `loc_llm_at` store-owned; all out of `content_hash`). Cluster membership itself is **not** on
  the row — it lives only in the disposable §17 overlay tables (`position_identity_keys`,
  `position_clusters`; §4.12 adds `location_values`, `position_locations`,
  `geo_region_members` on the same disposable/durable terms).
- **`Profile` / `ProfilePrefs`** — §5 profile (below); `profile_hash` keys the match cache.
- **`MatchVerdict`** — `score: int` (0–100 clamped), `verdict: strong|good|weak|reject`, `reasons`,
  `concerns` (§4.7). `PROMPT_VERSION = 'match-v2'`. The match-time extractor (§4.7) is a separate
  signature cached under `EXTRACT_VERSION = 'extract-v3'` (v2 added §4.12's `location_geo`;
  v3 the head+tail digest window) —
  keyed on `(content_hash, model)` with **no** `profile_hash`, since salary/employer/geography
  are properties of the posting, not the profile.
- **Run record** (canonical) — `{site, started, finished, positions_found, new_positions, errors,
  pages_fetched, signals}` (the `runs` table's `items_found`/`new_items` columns carry
  positions/new-positions; the richer per-crawl deltas — changed/closed/matched/pending — plus
  `description_quality` ride in `signals`). §4.8/§4.9 reference this.
- **`CrawlSummary`** — the run's counters: `site, positions_found, new_positions, changed_positions,
  closed_positions, errors, pages_fetched, incomplete, matched, match_pending`.
- **`CandidateResult`** — frozen model (`scoring.py`): `item_count`, `samples` (capped),
  `schema_errors`, `pages_fetched`, `exception:{type,message,traceback_tail}|None`. The §6 scoring-core
  output; all §4.11 adapters + the feedback transform + §8 admission read these exact fields.
- **Trainset example** — `dspy.Example(site_url, corpus, render, company, hints, discovery_*,
  crawler_source).with_inputs(...)`; inputs-only (§4.11).

### 5a. Profile (`PROFILE.md`, `profile.py`)
`PROFILE.md` (repo root, committed; `JOBCRAWLER_PROFILE`) is the single source of *what the operator
wants*, split into two halves so the cheap deterministic layer and the expensive LLM layer each read
the part they need:

- **YAML frontmatter** (between `---` fences) → `ProfilePrefs`, the machine-readable preferences the §6
  prefilter reads to drop definite mismatches *before* any token is spent. Every key is **optional** —
  an empty/omitted key means "no constraint" (recall-safe; the LLM still judges it):
  `disciplines`/`levels`/`workplace` (frozensets **validated against the §4.6 enums** — an unknown
  value fails loud at load, naming it), `locations`/`languages`/`exclude_keywords`/`exclude_companies`/`exclude_locations`
  (free-text tuples — the last drops a *remote* posting whose stated location is scoped to a region
  needing work authorization the operator lacks, e.g. `remote, us`; matched case-insensitively against
  the location and gated to remote so worldwide/timezone-overlap roles still pass), and
  `min_salary: SalaryFloor(amount, currency, period)` (structured, so a
  comparison against a job's parsed salary needs no NL parsing; **parsed and displayed today —
  prefilter enforcement is deliberately unwired**: dropping salary-less postings would discard
  the majority of the corpus, so the operator floor is the §16 `min_salary` browse filter over
  the USD band; wiring the pref into the prefilter is a named follow-up).
- **free markdown body** → the rich narrative (skills, domains, what to reject) handed **verbatim** to
  the matcher LM as context.

`Profile(raw, body, prefs, profile_hash)` is content-addressed by `profile_hash` = sha256 of the
*normalized raw text* (line-endings unified, trailing whitespace stripped, so cosmetic churn doesn't
bust the cache). Because the hash is over the **raw** text — prefs *and* body — a body-only edit (new
LLM context) also invalidates cached verdicts (§4.7). `load_profile(path)` raises
`ProfileNotFound` (a `FileNotFoundError` subclass) with a create-`PROFILE.md` hint; YAML is parsed with
`yaml.safe_load` (operator config, not code).

## 6. Generation acceptance gates
A generated script is accepted only if it passes, in order:
1. **Static analysis** — AST allowlist; no banned imports/builtins; matches the §4.3 contract.
   Consumes `crawler_source.code` (fences already stripped — §4.2). **(b) Host admission (§8):**
   `SITE`'s host must be the registry entry's (a drifted SITE would derive the whole allowlist
   from an unregistered base), and every declared `EXTRA_HOSTS` entry must be a literal bare
   hostname that is (i) **evidence-backed** — matched against hosts the site's own served corpus
   vouches for, (ii) within `JOBCRAWLER_EXTRA_HOSTS_CAP`, and (iii) clean under the fixed SSRF
   kernel. Gates 2–3 then *execute* against **exactly the committed allowlist** (registry ∪
   declared), never the wider generation-time exploration envelope — a fetch production would
   refuse fails here, not on the first scheduled crawl.
2. **Runtime validation & completeness** — yields ≥ `min_items`; every posting has a non-empty
   `title`/`company`/`location`, a **sufficiently long, non-boilerplate** `description` (≥
   `min_description_chars`, ≥ `min_unique_description_chars` distinct chars, and reject an
   `expired_markers`/mojibake body), and a valid `url` on an **evidence-backed host** — the committed
   allowlist *or* a host the site's own served corpus names (the same §8 evidence rule as gate 1(b),
   but robots-**unfiltered**: robots governs what may be *fetched*, not what a posting may *link* to).
   An ATS API's canonical posting link legitimately lives on a sibling human-facing host the crawler
   never fetches — Greenhouse's `absolute_url` sits on `job-boards.greenhouse.io` while the crawl host
   is `boards-api.greenhouse.io`; judging sample urls against the crawl allowlist alone makes every
   API-first board ungeneratable. Runs within time/page/memory budget;
   obeys robots. Plus two anti-under-enumeration checks — enumerating a board is an
   *exhaustive-enumeration* task and RLMs reliably under-enumerate on "find every X" [Sanjaya]: (a)
   **must-observe** — reject/down-score a candidate that never called `validate_crawler` before
   `SUBMIT`, or chose `discovery_method="scrape"` without first probing for an ATS API / `find_feeds` /
   `get_sitemap` (the docstring's "API-first" is *enforced*, not merely requested); (b) **enumeration
   floor** — compare the crawler's posting count against the host-side oracle in the corpus (a
   Greenhouse `meta.total`, an Ashby array length, the count of `postings` entries), flagging a scraper
   that grabbed the visible page and missed the API. **The `meta.total` gotcha:** for Gupy do **not**
   trust `total` at `limit=100` — the oracle is "page until a short page", not the first page's `total`
   (§4.2).

   **`min_items` is 1, on purpose (the news→jobs inversion).** A small startup careers page with **one
   or two** open roles is a *legitimate, complete* board — so an absolute floor of 1 only rejects the
   truly empty crawl. Completeness is instead carried by the **relative enum-floor** (did we get
   roughly as many as the board advertises?) and the stability gate — both of which a genuinely small
   board passes and a broken selector fails. The old news `min_items=5` would wrongly reject a correct
   two-role crawler here.

   **Schema errors tolerate a production-mirroring ratio.** The production runner (`runner._collect`)
   drops an invalid yield, counts it (`errors=N`), and the crawl proceeds — so gate 2 fails a candidate
   only when schema errors exceed `max_schema_error_ratio` (default 2%) of total yields. One remote
   posting with an empty `location` on a hundred-role Gupy board is a wart production would quietly
   skip, not a broken extractor; a systematic mapping bug still fails, and mass breakage also collapses
   `item_count` into the floors above. Tolerated errors stay visible in `validate_crawler` output, so
   the write → validate → fix loop still steers toward zero.
3. **Stability** — two runs overlap by **Jaccard of canonical URLs ≥ a *low* global threshold**
   (`stability_jaccard`, §4.6 canonicalizes `url`): a hard reject only for the **near-random** case,
   with near-threshold overlap surfaced as *soft* feedback for the §8 review path. **Excluded from the
   optimizer scalar** (§4.11), so a false reject can't poison selection/GEPA.
4. **Review gate** (§8) — per `JOBCRAWLER_REVIEW_GATE` (tri-state): `auto` (default) backs up the live
   crawler and promotes the new one unattended (safety = the gates + versioned rollback); `on` stages
   it under `crawlers/_pending/<slug>.py` for a human `jobcrawl promote`; `off` writes live with no
   backup.

**The INVERTED boilerplate gate — the load-bearing §6 change.** The news spec rejected a body with too
much *shared* text across items, on the theory that identical boilerplate means a broken extractor
grabbing chrome. **That predicate rejects a *correct* job crawler.** Postings on one board legitimately
share large blocks — the same benefits paragraph, the same equal-opportunity statement, the same
company blurb, the same "how we work" section — and a Lever/Greenhouse board often repeats an identical
company boilerplate on *every* posting. So the gate is inverted into three parts:
- **Shared blocks are allowed** — cross-posting text overlap is *expected*, never penalized.
- **Unique-residual floor** — what must clear a bar is the **per-posting *unique* residual** after the
  board's shared blocks are subtracted: each posting must contribute enough of its *own* text (the
  role-specific responsibilities/requirements) — this is what `min_unique_description_chars` (§11)
  operationalizes. A crawler that yields only the shared boilerplate (title + benefits, no role body)
  fails here even though its raw length clears `min_description_chars`.
- **Identical-description reject, title-aware** — byte-identical descriptions under **different**
  titles mean the crawler grabbed a board-level blob and stamped it on each row → reject. An identical
  body under the **same** title is a legitimate per-region clone (one req posted per location, the
  GitLab/Greenhouse shape) → tolerated; the shared-block set is accordingly computed over **distinct
  description strings**, so a clone can't promote its own body to "boilerplate" and residual-strip
  itself to zero. A sample set that collapses to a *single* (title, description) posting still
  rejects — that is one job echoed n times, not a crawl.

Gates 1–2 are computed by **one pure scoring core** returning a `CandidateResult`, a `gate_failures`
reason string, and a **graded, continuous, multi-axis** score — *not* a 0/1 verdict. Compute the hard
pass/fail gates **first**, then combine the quality axes over survivors by **shape-by-necessity** — a
**min/AND** veto only for true veto axes (on-domain-URL validity; field-presence) and a **bounded
weighted sum** for the graded axes (postings-yielded ratio, unique-residual description rate,
discovery-method quality), every axis **bounded to [0,1]** (the postings axis is `min(count/N, 1)`,
clamped). **Every working precedent that collapsed quality axes used additive/weighted-sum**
[Netflix `#77d24b`, R1 `#494c25`, ReSeek `#fe1014`, retrieval-fusion `#dd2c1f`]; pure-multiplicative
count is 0. **Axis weights** are seeded *non-uniformly* (heaviest on postings-yielded + unique-residual
"real-postings" core, lightest on discovery-method — the `#e643b2` prior) with the **fewest free
weights**, precision axes above recall [ReSeek]; run a **leave-one-axis-out** ablation before freezing
discovery-method (a low-signal axis can hurt more than mis-weighting [Sotopia-RL]). Keep the noisy
stability gate **out** of the scalar. The **same scoring core** feeds the §4.11 optimizer through the
three adapters.

**Candidate selection.** Generate N candidates (`fanout_n`, default 4) and take the **argmax over the
graded scalar**. They run **sequentially** (stock `dspy.BestOfN`/`dspy.Refine` are sequential for-loops
with an early break, verified in installed 3.2.1) — so N costs ~N× wall-clock and ~N× tokens (sized
against `FANOUT_TOKEN_CAP`); the real win is the **early break**, not parallelism. For feedback-carrying
retries use a **small custom loop**, not stock `dspy.Refine` (its hint comes from
`inspect.getsource(reward_fn)`, never our `gate_failures`): add a `prior_feedback` `InputField` and
re-invoke generation with the prior `CandidateResult.gate_failures` injected; cap retry depth.

An attempt that dies **before producing a prediction** (an LM/adapter failure — unparseable model
output, a dropped stream — as opposed to a graded-zero candidate) does not consume a comparison slot:
the fan-out redraws with a fresh `rollout_id`, up to `fanout_retries` extra draws
(`JOBCRAWLER_FANOUT_RETRIES`, default = `fanout_n`, so total attempts stay ≤ 2N and the spend cap
still applies). Without this, a reasoning model that intermittently emits unparseable JSON zeroes out
a site's entire fan-out by pure rollout roulette and burns a 6h-backoff slot on it.

## 7. Project layout
```
jobcrawler/
  pyproject.toml            # name="jobcrawler"; [project.scripts] jobcrawl = jobcrawler.cli:main
  flake.nix .envrc          # nix dev shell (Deno + Playwright browsers provisioned here)
  sites.yaml                # the input board registry (§4.1)
  PROFILE.md                # the operator's match profile (§5a)
  src/jobcrawler/
    config.py               # all env knobs in one place (§11)
    sites.py                # load/validate sites.yaml → list[Site]
    profile.py              # PROFILE.md → Profile/ProfilePrefs; profile_hash (§5a) — NEW
    match.py                # host-side batched/cached LLM matcher: MatchJobs, prefilter, Matcher (§4.7) — NEW
    tracking.py             # operator status policy: TrackStatus enum, labels/colours, applied-implies (§16) — NEW
    generate/               # generation + self-improvement
      signatures.py         #   GenerateCrawler (+ owned strategy sub-signature, §4.11)
      tools.py              #   fetch/render/find_feeds/get_sitemap/validate_crawler
      snapshot.py           #   build the prefetched corpus dict: homepage/robots/sitemap/postings (§9)
      generator.py          #   CrawlerGenerator(dspy.Module): RLM + BestOfN/Refine; save/load (§4.11)
      scoring.py            #   one scoring core + reward_fn / Evaluate / GEPA adapters (§6, §4.11)
      gates.py adversarial.py budget.py evaluate.py optimize.py  # §6 gates, §8 cross-val, caps, §4.11
    runtime/                # the crawl phase (no LLM)
      contract.py           #   Job model + crawl(ctx) protocol (§4.3/§4.6)
      normalize.py          #   deterministic discipline/level/workplace/salary enrichment (§4.6) — NEW
      ctx.py live_ctx.py    #   fetching + politeness + challenge escalation (§4.5)
      solver.py ssrf.py     #   ChallengeSolver + clearance cache (§4.10); SSRF guard (§8)
      sandbox.py hardening.py safe_exec.py  # constrained execution + XML hardening (§4.4, §8)
      runner.py             #   import → crawl → validate → lifecycle → enrich → match (§4.7)
    state.py                # positions/match lifecycle + run/health store (§4.7); operator tracking axis + crawl-request seam (§16) — extended
    schedule.py adaptive.py governor.py durations.py  # scheduler + adaptive cadence + pacing (§4.9)
    health.py               # staleness detection → regeneration (§4.8)
    generator_pointer.py adopt_watch.py optimize_job.py trainset.py  # §4.11 versioning/adoption/trainset
    notify.py metrics.py    # operator notifications (§4.7/§4.8/§4.11) + observability (§4.9)
    cli.py cli_generate.py cli_optimize.py  # operator commands (§10)
    web/                    # §16 FastAPI tracker mounted in-process by `jobcrawl run` over the shared store — NEW
      app.py                #   create_app(store) factory + serve_app(); routes, JSON api, presentation helpers
      templates/            #   Jinja2: base/index/detail/dashboard/applications (browse, funnel, applied pipeline)
      static/               #   app.css (dark theme) + app.js (progressive-enhancement auto-submit)
    crawlers/               # GENERATED, committed: <slug>.py; _pending/ staging, _history/ backups
    generators/             # GEPA-optimized generator-N.json artifacts (JSON, versioned; §4.11)
  tests/                    # mirror src; MockInterpreter (dspy ships none), recorded HTTP + golden fixtures
```
`ingest.py` / `NewsClient` and every `POST /news` reference are **removed** — the matching pipeline
(§4.7) is the replacement sink.

## 8. Security model (generated code that runs on a schedule)
The central risk is **executing model-written code against the live web**. Mitigations:
- **AST allowlist** on every generated module (imports, builtins, no dynamic exec).
- **Sandboxed execution** (`runtime/sandbox.py`): subprocess with CPU/memory/time limits, no filesystem
  writes, **network egress only via the `ctx` proxy**.
- **SSRF guard** in `ctx`: requests restricted to the board's registered host(s) and on-domain links;
  block private/loopback/link-local ranges and cloud metadata endpoints.
- **Inferred allowlist — policy is inferred, enforcement is fixed (§4.3/§6 gate-1(b)).** The
  autonomous loop may *choose* which public hosts a crawler talks to (a Getro board's postings ride
  `api.getro.com` while the served HTML only ever names `cdn.getro.com`), but only within **evidence
  computed by trusted host code** over the site's own served bytes: literal `https://<host>` tokens ∪ a
  TCB-owned board-platform map ∪ the known-ATS API candidates — kernel-filtered, the base host
  excluded, and each candidate's `robots.txt` probed at admission (a blanket-`Disallow` host —
  `api.getro.com` is `Disallow: /` for every agent — is dropped from evidence entirely: it would pass
  bare-ctx generation and then die on its first polite production crawl, wedging the site in a heal
  loop). Generation *explores* the envelope (registry ∪ robots-eligible evidence); the winner must
  *declare* what it uses (`EXTRA_HOSTS`); gate-1(b) admits declarations only against that evidence and
  a small cap; and the §6 runtime gates, trainset record, and committed crawl all run on **registry ∪
  declared** only. Declaration alone authorizes nothing — the generation context is
  attacker-influenced (a job page can carry injected instructions) — and the hard IP/metadata denials
  apply to every host regardless of provenance. The offline optimizer metric (§4.11) recomputes the
  same evidence bound from each example's frozen corpus; it skips the robots probe (a delivery-time
  concern), a deliberate optimism bias noted in `cli_optimize`.
- **Review gate**: generated/regenerated scripts are gated per `JOBCRAWLER_REVIEW_GATE` before being
  enabled (§6 gate-4) — `auto` (gated by the §6 gates + versioned rollback), `on` (human `promote`), or
  `off`.
- **The harness is an immutable trusted computing base.** The acceptance gates, sandbox policy, AST
  allowlist, SSRF guard, **and every host-side threshold** (the §6 gate numbers, the §4.6 completeness
  floors, the §4.7 closure threshold, the §4.7 match `daily_cap`/`min_score`) are *never* authored,
  parameterized, fetched, or relaxed by model-generated code or by the GEPA-optimized predictor — only
  by version-pinned host code/humans. GEPA may rewrite the strategy instruction but can **never** widen
  the allowlist or weaken a gate.
- **Hide the scorer from the optimized loop.** The scoring core's exact acceptance predicates and
  numeric thresholds are not exposed to the generator/RLM: feedback says *what* failed semantically ("0
  postings extracted", "descriptions truncated"), never the predicate/threshold a candidate could
  target. Objective-hacking rose specifically when the scorer was *visible* to a self-modifying agent
  [Darwin-Gödel-Machine]; under-specified self-evolution drove reward-hacking up to 78%
  [Self-Evolving-survey].
- **Adversarial admission.** The reward (posting-count + field-presence + stability) is a gameable proxy
  — a crawler could pad/fabricate postings or scrape off-domain. Before a pair is admitted (§4.11),
  cross-validate its postings against independent signals (URLs resolve, titles match the live page,
  duplicate/boilerplate-body penalty via the §6 unique-residual measure, off-domain penalty).
  Acceptance rests on the **actual** `CandidateResult`, never the model's *claimed* counts.
- **Untrusted-XML/JSON hardening (hot path).** Feed/sitemap parsing consumes attacker-influenced XML on
  *every committed crawl*; disable external-entity resolution and entity expansion (`defusedxml` /
  `resolve_entities=False`) to block XXE / billion-laughs. The parser libs are part of the
  **pinned-image TCB** — a parser CVE is fixed by re-pinning, not regenerating a crawler. (The §4.4
  timeout + memory cap backstop regex backtracking / RAM exhaustion on a multi-MB board payload.)
- **The matcher is host-side, spend-capped, and off the crawl surface (§4.7).** The one new LLM surface
  runs **outside** the crawl sandbox, on the host, bounded by `match_daily_cap` (hard per-UTC-day) +
  `match_rate` (governor) + the content/profile cache (score-at-most-once). The **profile content is
  sent only to the configured match LM** (`require_match_model` resolves `JOBCRAWLER_MATCH_MODEL` else
  the cheap `sub_lm`, **never** the root planner — a per-token safety *and* a privacy boundary) and is
  never exposed to the crawler generator/RLM or fed back into its optimizer loop. The match verdict's
  reasons/concerns are **operator-facing**; a separate offline, operator-invoked builder
  (`jobcrawl match-trainset`) may project them — with the operator's own accept/reject feedback
  overruling the stored score — into a trainset for distilling a cheaper **matcher**, but they are never
  read back by the **crawler generator/RLM** (that would breach the §8 hidden-scorer invariant). No LLM
  scores its own output in the §6 gate; every signal the §6 scorer reads is host code the optimized loop
  can't touch.
- **The web tracker is a loopback-bound read/write store client, not a new trust boundary (§16).** `jobcrawl
  serve` runs no scheduler and no LLM: it reads the store and flags work (`request_crawl` / requeue) for the
  daemon to drain — it never imports a crawler, executes generated code, or calls a model, so it can't widen
  the §4.4 sandbox surface. It holds **personal application data** (which roles you applied to, private notes),
  so it binds `127.0.0.1` by default (`JOBCRAWLER_SERVE_HOST`); `0.0.0.0` is opt-in and only sound behind a
  trusted reverse proxy with auth. The operator tracking axis (`track_status`/`notes`/`dismissed`) is, like the
  match reasons above, **operator-facing** — no *crawler* generator/optimizer code path reads these columns, so the
  UI can't become a channel into the §8 hidden-scorer invariant; only the offline `match-trainset` projector reads
  them, and solely to correct **matcher** training labels.

## 9. DSPy / runtime notes
- `dspy.RLM` is `@experimental` — pin DSPy `3.2.x`.
- The **default RLM sandbox requires Deno** (Pyodide/WASM); provision it in `flake.nix`.
- RLM's Pyodide sandbox has **no network** — the model reasons over the prefetched corpus and reaches
  the live board only through the host **tools** (§4.2), never from inside the sandbox.
- **Two distinct sandboxes:** RLM's Pyodide REPL (where the model *reasons and drafts*) and the host
  execution sandbox (§4.4, where crawlers actually *run*). `validate_crawler` is the bridge.
- **The matcher does not touch either sandbox.** `dspy.Predict(MatchJobs)` is a plain host-side
  prediction under `dspy.context(lm=build_lm(model, temperature=0.0, …))` — no Pyodide, no Deno, no
  crawler execution. Its LM is bound per batch (never a process-global `configure`), mirroring the
  generator.
- **`corpus` injection.** `corpus` is a plain **JSON-able dict** passed as an RLM input; dspy's
  `_serialize_value` injects it as a Python literal and **raises `CodeInterpreterError` on any non-JSON
  type**; values over 100 MB spill to `/tmp` and are `json.loads`-ed in-sandbox. So shape `corpus` as an
  **addressable dict** (`{homepage, robots, sitemap, postings:[{url,title,text}]}`) of JSON primitives —
  addressable parts beat one blob [Coding-Agents]. Capture `crawler_source` via the REPL **FINAL
  channel** (bypasses the output-token limit); gate-1 treats a non-parsing `crawler_source` as truncation.
- **Interpreter left at default Pyodide/Deno** — a host-CPython interpreter would run the model's
  *ungated* exploratory code in a network-capable process, dissolving the no-network isolation §8 relies
  on.
- Obtain the LM via `dspy.context`, not the thread-owned `dspy.configure`. Candidate distinctness comes
  from `rollout_id` + temperature, **not** `cache=False` (§4.2). Use `aforward`/async.
- **Order gates cheapest-first**, short-circuiting on first failure: AST → single-run yield → two-run
  Jaccard stability → (offline only) multi-pass promotion eval.
- **Every DSPy-internal claim is source-pinned, not smoke-tested** — each load-bearing assumption is
  recorded with the dspy version (3.2.1) and the `file:line` it holds at (`rollout_id` in the cache key;
  `_serialize_value` type behavior; `Evaluate` metric arity; `valset = valset or trainset`; 5k+5k
  truncation), re-audited on any dspy version bump, with **no runtime smoke test** (per project
  preference).
- **LM construction is centralized in `build_lm` (`lm.py`) so a `chatgpt/*` slug bills against a
  ChatGPT subscription instead of per-token OpenRouter credits.** Every LM site (RLM root/`sub_lm`,
  GEPA `reflection_lm`, the §4.7 matcher) is built through `build_lm`; a plain `openrouter/…` slug is
  constructed exactly as before, while a `chatgpt/<model>` slug is routed to litellm's native ChatGPT
  provider — the Codex *Responses* backend — by forcing `model_type="responses"` and **dropping
  `temperature`/`max_tokens`** (that backend ignores them, and dspy's reasoning-model guard
  (`clients/lm.py`, 3.2.1) *raises* for a `gpt-5*` slug given `temperature≠1` or `max_tokens<16000` —
  exactly the matcher's shape). litellm's provider **prepends the Codex coding-agent system prompt** to
  every request (unavoidable on that backend): natural for crawler generation, tolerated for matching
  via the explicit output contract.
- **We write no OAuth flow.** The token comes from the official `codex login` (`~/.codex/auth.json`);
  `jobcrawl login` (`auth.py`) imports it into litellm's store under `JOBCRAWLER_AUTH_DIR`, reusing
  litellm's own `_build_auth_record`/`_write_auth_file` so the derived `expires_at`/`account_id` match
  what its provider reads back. The **`refresh_token` is load-bearing** — the imported access token is
  a short-lived JWT, so litellm mints a fresh one on first use. `run`/`crawl` **preflight** validates
  the token **non-interactively** (refresh-or-fail, *never* litellm's blocking 15-min device-code
  prompt), so a `chatgpt/*` deployment with a dead credential fails fast with a `codex login` hint
  rather than hanging the daemon.

## 10. CLI & operations
| command | does |
|---|---|
| `jobcrawl run [--host --port] [--no-web]` | **start the scheduler daemon + the §16 web tracker UI** in one process — crawl due boards, track lifecycle, match, retry, regenerate (§4.9), and serve the UI over the same store (loopback-bound by default; `--no-web` for headless) |
| `jobcrawl generate <slug> \| --all \| --missing \| --stale` | run RLM, gate (§6), write `crawlers/<slug>.py` (`--missing` = every enabled site with no committed crawler, to drain the §4.2 cold-start backlog operator-side) |
| `jobcrawl generation-log [site] [-v]` | recent generation attempts (outcome + reason); `-v` dumps each attempt's captured **LLM interaction log** — the durable "why did generation fail" (§4.2) |
| `jobcrawl validate <slug>` | dry-run a committed script through the §4.4 sandbox, report positions/errors, **no state/match** |
| `jobcrawl crawl <slug> \| --all` | run committed scripts, dedup, track positions, match new/changed |
| `jobcrawl promote <slug>` | move a self-heal crawler staged under `crawlers/_pending` (§8) to live |
| `jobcrawl rollback <slug>` | restore a board's prior crawler from `crawlers/_history` (undo an `auto` promotion) |
| `jobcrawl list` | show boards, script presence, last position count, health/staleness, learned cadence |
| `jobcrawl status` | per-board run aggregates, fleet health distribution, drift-recovery MTTR, active generator + held-out score + autonomy brake (§4.9/§4.11) |
| `jobcrawl optimize [--adopt]` | the offline §4.11 job: build trainset → optimize → held-out promote (`--adopt` flips the active-generator pointer) |
| `jobcrawl match-trainset [--out PATH]` | project persisted match verdicts + operator feedback into a JSONL **matcher** trainset for distilling a cheaper matcher (§8); §17 cluster-inherited verdicts are excluded (copies, not judgments) |
| `jobcrawl dedup [--rebuild \| --report \| --block-key K [--reason R] \| --unblock-key K \| --requeue-inherited \| --seed-notify-ledger]` | operate the §17 identity overlay: recompute it from scratch, print the last sweep report + live overlay stats + blocklist, split an over-merging key (operator kill-switch), reverse every inherited verdict into the pending queue, or re-seed the notify ledger |
| `jobcrawl retire-site <slug>` | close all open rows of a removed board (`reason=site_retired`) — the DB half of retirement; removing/disabling the `sites.yaml` entry is the registry half. History, verdicts, and §17 suppression facts survive |
| `jobcrawl rollback-generator` | revert the active generator to the previously-adopted version (§4.11) |
| `jobcrawl login` | import the ChatGPT-subscription (Codex) OAuth token from `codex login` into the store a `chatgpt/*` model slug bills against (§9) — one-shot, not a daemon |

`jobcrawl run` is the primary deployment entrypoint (the service schedules itself); `crawl` stays for
manual one-shot runs and CI. Startup **preflight** fails fast on a missing/malformed `sites.yaml`, an
unwritable state path, or a missing `PROFILE.md`.

## 11. Configuration (env, all in `config.py`)
All knobs are `JOBCRAWLER_*` (exceptions: `FLARESOLVERR_URL`, and the ChatGPT-OAuth interop env
`CHATGPT_TOKEN_DIR`/`CODEX_HOME` honored by §9) and host-side (never exposed to the generator — §8).
Any model slug above may be `chatgpt/<model>` to bill against a ChatGPT subscription (§9) instead of
an `openrouter/…` slug billed per token. Selected knobs:

| var | default | meaning |
|---|---|---|
| `JOBCRAWLER_MODEL` | `chatgpt/gpt-5.6-sol` | RLM root planner/extractor — a strong, code-capable model; override for a different provider/slug |
| `JOBCRAWLER_SUB_MODEL` | `chatgpt/gpt-5.6-luna` | RLM `sub_lm` — a cheap model so in-sandbox `llm_query` never bills at root rates |
| `JOBCRAWLER_REFLECTION_MODEL` | *(required for GEPA)* | GEPA `reflection_lm` (§4.11), ideally a different family |
| `JOBCRAWLER_MATCH_MODEL` | `chatgpt/gpt-5.6-luna` | the host-side §4.7 match LM — a cheap model; `require_match_model` **never** falls back to the root planner |
| `JOBCRAWLER_AUTH_DIR` | `~/.config/jobcrawler/chatgpt` | store for the ChatGPT-subscription OAuth token a `chatgpt/*` slug uses (§9); `jobcrawl login` writes it, litellm reads+refreshes it. Falls back to `CHATGPT_TOKEN_DIR`. A token store, not an API key |
| `JOBCRAWLER_MATCH_BATCH` | `10` | jobs per `dspy.Predict(MatchJobs)` call (§4.7) |
| `JOBCRAWLER_MATCH_MIN_SCORE` | `70` | 0–100 score at/above which a match notifies (surface threshold, not a drop) |
| `JOBCRAWLER_SCORE_HALF_LIFE_DAYS` | `30` | §4.7 freshness decay: the *effective* score's bonus above the floor halves every this-many days of posting age (from `posted_at`, else `first_seen`), so a newer posting outranks an equally-good older one |
| `JOBCRAWLER_SCORE_FRESHNESS_FLOOR` | `0.6` | §4.7 freshness-decay floor in [0,1]: the smallest recency multiplier, so a great fit stays represented (never below 60% of its raw score); `1.0` disables decay |
| `JOBCRAWLER_MATCH_DAILY_CAP` | `2000` | hard per-UTC-day LLM-scored jobs; cache hits free; overflow → `match_pending` |
| `JOBCRAWLER_MATCH_DRAIN_FLOOR` | `160` | §4.7 slice of the daily cap fresh scoring holds back for the backlog drain (fresh spends ≤ `cap − floor`; drain spends the floor + fresh's leftover); `0` = one shared budget |
| `JOBCRAWLER_MATCH_RATE` | `20/m` | governor pacing for match LLM calls (≠ the daily volume cap) |
| `JOBCRAWLER_MATCH_CONCURRENCY` | `4` | §4.7 scoring batches in flight; the governor still paces every batch START, so this overlaps model latency without raising the request rate (`1` = sequential) |
| `JOBCRAWLER_EXTRACT_MODEL` | `sub_model()` fallback | the §4.7 match-time salary/employer extract LM; `require_extract_model` falls back to the cheap `sub_lm`, **never** the root planner; unset with no `sub_lm` ⇒ extraction is simply **off** |
| `JOBCRAWLER_EXTRACT_MIN_SCORE` | `60` | 0–100 match score at/above which a salary-less posting is mined (below the notify threshold, above the noise floor) |
| `JOBCRAWLER_EXTRACT_CAP` | `50` | hard per-run ceiling on *fresh* (cache-miss) description extractions; cache hits free; overflow retried next run |
| `JOBCRAWLER_EXTRACT_LOCATIONS` | `true` | the §4.12 location arms of the §4.7 extract gate (ledger tail + marker); off ⇒ salary-only gate, no location writes/stamps; the deterministic parser is unaffected |
| `JOBCRAWLER_PROFILE` | `PROFILE.md` | path to the operator's match profile (§5a) |
| `JOBCRAWLER_FIRST_CRAWL_CAP` | `30` | newest-K new positions matched on a board's *first* crawl (§4.7) |
| `JOBCRAWLER_CLOSE_AFTER_MISSES` | `2` | consecutive absences from an enumerating board before a position closes (§4.7) |
| `JOBCRAWLER_RECHECK_INTERVAL` | `12h` | §4.9 recheck loop cadence (the only closure signal for `enumerates_all:false`) |
| `JOBCRAWLER_RECHECK_CAP` | `300` | max standing open positions re-fetched per recheck pass |
| `JOBCRAWLER_MIN_ITEMS` | `1` | §6 min positions (a 1–2 role board is legitimate; enum-floor+stability carry completeness) |
| `JOBCRAWLER_MIN_DESCRIPTION_CHARS` | `300` | §6/§4.6 min description length |
| `JOBCRAWLER_MIN_DESCRIPTION_QUALITY` | `0.6` | §6 fraction of sampled descriptions that must clear the length floor |
| `JOBCRAWLER_MIN_UNIQUE_DESCRIPTION_CHARS` | `150` | §6 unique-residual floor (the inverted boilerplate gate) |
| `JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR` | `0.5` | §4.8 partial-drift floor on the per-crawl `description_quality` |
| `JOBCRAWLER_EXPIRED_MARKERS` | 5 EN+pt-BR defaults | `\|`-separated closed-posting markers; empty disables (§4.6) |
| `JOBCRAWLER_STABILITY_JACCARD` | `0.6` | §6 gate-3 URL-overlap threshold |
| `JOBCRAWLER_ENUM_FLOOR_RATIO` | `0.5` | §6 enum floor vs the corpus posting-count oracle |
| `JOBCRAWLER_FX_ENDPOINT` | `open.er-api.com/v6/latest/USD` | §4.6/§4.9 keyless USD-based FX feed; inverted to USD-per-unit for `usd_annual_band` (attribution: exchangerate-api.com) |
| `JOBCRAWLER_FX_REFRESH_INTERVAL` | `24h` | §4.9 out-of-band FX-rate pull cadence; `off` disables (static `FX_TO_USD` floor then carries conversion) |
| `JOBCRAWLER_STATE_URL` | `sqlite:///jobcrawler.db` | position/match/run/health/tracking store (§4.7/§16) |
| `JOBCRAWLER_SERVE_HOST` | `127.0.0.1` | `jobcrawl run` web UI bind address (§16; `--host` overrides); **loopback by default** — the UI holds personal application data, so `0.0.0.0` only behind a trusted proxy/auth |
| `JOBCRAWLER_SERVE_PORT` | `8765` | `jobcrawl run` web UI listen port (§16; `--port` overrides) |
| `JOBCRAWLER_USER_AGENT` | `jobcrawler/0.1 (+job tracker)` | sent on every request |
| `JOBCRAWLER_RATE_LIMIT` | `2/s/host` | §4.5 politeness cap |
| `JOBCRAWLER_MAX_PAGES` | `200` | per-crawl page budget (sized for paginated boards, §4.5) |
| `JOBCRAWLER_EXTRA_HOSTS_CAP` | `3` | §6 gate-1(b) max declared `EXTRA_HOSTS`; the blast-radius bound atop the evidence rule (§8) |
| `JOBCRAWLER_DEFAULT_SCHEDULE` | `2h` | fixed cadence for a board with no `schedule` and adaptive off |
| `JOBCRAWLER_ADAPTIVE_DEFAULT` | `on` | a board with no explicit cadence is adaptive (§4.9) |
| `JOBCRAWLER_MIN_SCHEDULE` / `MAX_SCHEDULE` | `30m` / `3d` | adaptive-interval politeness floor / staleness ceiling |
| `JOBCRAWLER_CONCURRENCY` | `8` | max boards crawled concurrently |
| `JOBCRAWLER_AUTONOMY` | `on` | master brake over all self-modification (heal/regen/promote/reopt/adopt/rollback) |
| `JOBCRAWLER_REVIEW_GATE` | `auto` | tri-state delivery: `auto` (promote+backup) / `on` (stage for `promote`) / `off` (write live) |
| `JOBCRAWLER_REGEN_CHECK` | `24h` | staleness-evaluation cadence (§4.8) |
| `JOBCRAWLER_HEAL_COOLDOWN` | `6h` | min spacing between event-driven self-heals of one board |
| `JOBCRAWLER_REOPT_CHECK` | `24h` | offline §4.11 dispatch cadence (`off` disables) |
| `JOBCRAWLER_TRAINSET_STABLE_RUNS` | `3` | sustained healthy runs before a pair enters the trainset (§4.11) |
| `JOBCRAWLER_PROMOTION_EVAL_PASSES` | `3` | held-out eval passes averaged per promotion decision |
| `JOBCRAWLER_ADOPT_PROBATION` | `48h` | post-adoption window the §4.11 rollback watcher judges (`off` disables) |
| `JOBCRAWLER_ADOPT_REGRESSION_DELTA` | `2` | extra generation-failures over baseline that trip auto-rollback |
| `JOBCRAWLER_MAX_ITERATIONS` / `MAX_LLM_CALLS` / `MAX_SUBCALLS` | `20` / `40` / `60` | RLM caps + the unconditional host sub-call kill (§4.2) |
| `JOBCRAWLER_FANOUT_N` / `FANOUT_TOKEN_CAP` | `4` / `2M/site` | §6 best-of-N width + the aggregate fan-out token ceiling (dspy records tokens for every provider; USD isn't priced for many slugs) |
| `JOBCRAWLER_FANOUT_RETRIES` | `= FANOUT_N` | §6 redraws for LM/adapter-dead attempts (a failure never eats a comparison slot; attempts ≤ 2N) |
| `JOBCRAWLER_BOOTSTRAP_BUDGET` | `2m` | §4.9 per-tick time budget for the cold-start bootstrap pass (over budget → roster defers to next tick) |
| `JOBCRAWLER_BOOTSTRAP_CONCURRENCY` | `6` | §4.9 max cold-start generations run in parallel per wave (1 = sequential; caps concurrent LM spend/rate) |
| `JOBCRAWLER_ASSESS_TIMEOUT` | `30m` | §4.9 liveness watchdog: max wall-clock ONE generation assess may hold the loop; on expiry the assess is abandoned (worker detached, its store writes still land) and the tick resumes — a wedged generation can't freeze crawling/recheck/matching. `0s` disables |
| `JOBCRAWLER_SOLVER` / `FLARESOLVERR_URL` / `JOBCRAWLER_CLEARANCE_TTL` / `JOBCRAWLER_EGRESS_PROXY` | `none` / — / `30m` / — | §4.10 challenge backend + clearance + IP/UA-pinned egress |
| `JOBCRAWLER_NOTIFY_WEBHOOK` / `NOTIFY_TIMEOUT` | — / `10s` | optional operator webhook for autonomous actions + match alerts (§4.7/§4.9) |
| `JOBCRAWLER_DEDUP` | `on` | §17 master switch: off stops the sweep loop + ingest key hook AND forces every consumer flag below to report off (the overlay tables persist, untouched) |
| `JOBCRAWLER_DEDUP_REFRESH_INTERVAL` | `15m` | §17 sweep cadence (first pass on boot; autonomy-exempt bookkeeping like recheck/fx); `off` disables the loop |
| `JOBCRAWLER_DEDUP_TIERS` | `0,1,2,3,6` | comma-set of identity tiers whose **edges** may join rows (keys for all tiers are always written — free shadow measurement). T4 corroborates only; T5 (fuzzy) ships off |
| `JOBCRAWLER_DEDUP_MATCH` / `_NOTIFY` / `_UI` | `on` | per-consumer flags (each ANDed with the master): cluster verdict inheritance (§4.7 cache step); notify-ledger sibling suppression; browse collapse/chips/siblings/distinct-KPI |
| `JOBCRAWLER_DEDUP_APPLY_PARAM_DENYLIST` | curated §17 list | tracking params stripped when normalizing `apply_url` for T2 identity — a SEPARATE, wider list than the crawl-time `tracking_param_denylist`; identity-bearing params (`gh_jid`/`ashby_jid`/`for`/`token`/`jk`/`pid`/`applyData`) are never on it |

## 12. Testing strategy
- **Generator** — `dspy.RLM` driven by a hand-authored `MockInterpreter` (dspy ships none importable;
  queued REPL responses) + `DummyLM`; assert tool wiring, that the scoring core + the `reward_fn`
  adapter score correctly, that a forced extraction (`final_reasoning`) is caught, and that bad output
  is rejected. No Deno, no live network.
- **Normalization (§4.6)** — pure in→out unit tests over the two-axis taxonomy and salary parser, **EN +
  pt-BR**: role-shape-outranks-IC precedence, `eng_other`/`non_eng` recall-safety, structured-workplace
  precedence, and conservative salary (`$189K–$330K`, `R$ 8.000 - R$ 12.000`, `€60k–80k`, ambiguous →
  `None`, BRL-defaults-monthly).
- **Salary parser v2 (§4.6)** — the widened-recognition table (dollar-family prefixes, ISO codes,
  shared-magnitude shorthand, entity dashes, Ashby intervals) and the refusal table (day/week rates,
  magnitude poison, ambiguous symbols, contradictory periods, zeros) pinned as unit tests alongside
  the original empirical/ambiguous tables (which pass unmodified — the conservatism contract);
  store-side: the repair-sweep classes each with idempotence + llm-untouched pins.
- **Geo parser (§4.12)** — the adversarial corpus pinned as unit tests: fence vs place derivation
  (`Remote - US` fences, `Cardiff, London or Remote (UK)` mixes), comma-list absorption, prefix-code
  disambiguation (`IN - Bangalore`→India, `Toronto, CA`→Canada), the documented `Georgia`/`Washington`
  acceptances, garbage-never-raises, purity/frozen invariants, and the region-members vocabulary;
  plus store-side ledger precedence (rules-clean-beats-llm), projection refresh/purge semantics, the
  open-time sweep, and the §17 pin that role-group buckets ignore `location_display`.
- **Profile (§5a)** — frontmatter split, enum validation (an unknown discipline fails loud naming it),
  `profile_hash` stability across cosmetic churn and its change on a body-only edit, `ProfileNotFound`.
- **Matcher (§4.7)** — with a fake `dspy.Predict` and a fake governor/clock: cache hit is free and
  applied; daily-cap overflow → `pending` (never dropped); an LLM/parse failure fails open to `pending`,
  but anything short of that degrades **per index**: prose around the JSON is salvaged (the first
  complete JSON value wins — a "fatal" chatty response would be cached and replay the failure forever),
  a padded array's stray verdict is dropped, a short array parks only the skipped job; the match LM call
  is token-bounded (`JOBCRAWLER_MATCH_MAX_TOKENS`, default 4000 — providers reject a call whose ceiling
  exceeds the remaining credit balance, so an unbounded request turns low credits into a matching
  outage); the digest truncates description to 1200 and prefers the parsed salary range; `PROMPT_VERSION`
  is a cache key; the prefilter drops only definite mismatches and passes `eng_other`/`unspecified`.
- **Position lifecycle (§4.7)** — `upsert_position` new/changed/unchanged + **reopen**; `close_missing`
  increments `missing_streak` and closes at threshold; a **failed/empty crawl never closes** (mass-closure
  guard); `expire_due`; `enumerates_all:false` skips the diff.
- **Self-improvement (§4.11)** — `dspy.Evaluate` over frozen corpus snapshots; the promotion gate refuses
  a non-improving candidate and one that regresses a stratum; `save`/`load` JSON round-trip; disjoint
  train/val/promotion pools; the **saturation** assertion fires on an all-near-maximal trainset; a tested
  **rollback**; `CrawlerGenerator.aforward` resolves without a missing wrapper `aforward`.
- **Runner/contract** — golden ATS-API/HTML/feed fixtures + recorded HTTP; assert normalized `Job`s,
  the full-description reconstruction (Lever concat, Greenhouse unescape), lifecycle transitions, and
  the enrich→prefilter→match wiring. Hand-written reference crawlers double as contract tests.
- **Politeness/SSRF** — unit tests proving robots/rate-limit/allowed-host enforcement.
- **Dedup overlay (§17)** — pure key-derivation tables over the measured hazard fixtures (gh 5-digit
  floor, org-conflict veto, generic-`apply:` corroboration, separator/blocklist splits, degenerate
  keys, HN company-tier exclusion); sweep determinism (same rows ⇒ same clusters/canonicals);
  election preferences; with every flag off the read queries are byte-identical to pre-§17; verdict
  inheritance (donor guards, no cache write, no spend) and its `--requeue-inherited` reversal;
  ledger gate (one ping per cluster, dismiss/apply suppression); collapse + anti-resurrection +
  user-data auto-force in `search_positions`.

## 13. Milestones (the news→jobs pivot)
- **P0 — Rename + green (DONE):** mechanical package rename `bizup_autocrawler → jobcrawler`,
  `AUTOCRAWLER_* → JOBCRAWLER_*`, `autocrawl → jobcrawl`; suite green (815 passed). No semantics changed.
- **P1 — Domain pivot (THIS CHANGE):** `Item → Job` with the rich optional fields (§4.6); host-side
  `normalize.py` (two-axis taxonomy + conservative salary); the `positions` lifecycle store + runner
  (upsert/close/reopen/expire, mass-closure guard, §4.7); `profile.py` + `PROFILE.md`; the host-side
  batched/cached/capped `match.py` matcher and its notify sink (replacing `POST /news`); the §6 `min_items=1`
  + inverted-boilerplate gate; the §4.1 `company`/`enumerates_all` fields; the §4.9 recheck/expiry loop;
  ATS-API-first generator seeds (§4.2) and posting-domain GEPA feedback (§4.11). Health/self-heal and GEPA
  mechanics **retargeted, not weakened**.
- **P2 — Real boards:** seed `sites.yaml` with real ATS boards (Greenhouse/Lever/Ashby/Gupy tokens +
  RemoteOK/WWR), generate over them, measure the §6 gate-1+2 pass-rate and $/board; the M1 step-0
  feed/API/Cloudflare tally (§4.10) sizing how much rides on the solver tier.
- **P3 — Cross-board dedup (DONE, §17):** the read-derived identity overlay — deterministic key
  tiers, guarded sweep, cluster verdict inheritance, notify-ledger suppression, UI collapse —
  shipped default-on with per-consumer kill-switches. Resolves §14 Q1 for exact/structural
  identity; the fuzzy tier (T5) remains off.
- **Next (post-v1):** **multi-profile** (per-persona `PROFILE.md`, per-profile caches/caps); **apply-assist**
  (draft a tailored cover note from the match reasons — still human-sent, never auto-applied); **richer
  salary normalization** (FX-normalized comparison **shipped** — `usd_annual_band` over DB-cached live
  rates, §4.6/§4.9; confidence bands still open — §14 Q3); **Postgres** state store
  (§14 Q5); §17 follow-ons: display-layer field borrowing onto the canonical, T5 fuzzy tier if the
  shadow reports ever justify it.

## 14. Open questions / follow-ups
1. **Cross-board repost dedup.** ~~The same role on Greenhouse + WWR + RemoteOK is three `positions` rows.
   Should a content-similarity pass collapse reposts (and how does closure interact — one board closes, the
   others don't)? v1 keeps them separate; a fuzzy `(company, title, normalized-description)` cluster key is
   the candidate.~~ **Shipped as §17** (structural tiers T0–T3 + T6, default-on): rows stay separate;
   identity is a read-derived overlay. The closure interaction resolved itself once identity left the
   rows — lifecycle stays per-board per-row, and canonical election simply prefers an open sibling, so
   one board closing never closes (or hides) a still-open copy. The fuzzy `(company, title)` key was
   built as T5 but stays **off** (measured false-merge hazards: robinhood 9× same-title reqs,
   seniority-stripped collisions); the §17 shadow report is the evidence bar for ever enabling it.
2. **Regeneration / review-gate delivery tier.** The delivery mechanism is *built* — `JOBCRAWLER_REVIEW_GATE`
   is the tri-state (§6 gate-4, §8): `auto` backs up the live crawler and promotes the new one unattended,
   `on` stages it under `crawlers/_pending/<slug>.py` for a human `jobcrawl promote`, `off` writes live with
   no backup. The open question is the *policy*: how aggressively should a self-heal (§4.8) auto-promote vs.
   stage vs. open a PR, and does that tier want to differ per-board (a flaky aggregator staged, a stable
   single-company board auto)? The tier trades directly against **drift-MTTR** (§4.8) — more human review
   lengthens time-to-recovery — so the "right" default is an operating-point choice, not a fixed one, and
   auto-promotion leans on the §6 gates + versioned rollback (`jobcrawl rollback`) as its only safety net.
3. **Salary inference confidence.** `parse_salary` is deliberately conservative (§4.6). The **FX-normalized
   comparison shipped** — `usd_annual_band` derives a `salary_usd_min/max` sort key over a static majors
   floor overlaid with a daily out-of-band live-rate pull (§4.6/§4.9), without touching the verbatim source
   figure. ~~Open: do we also surface a *confidence band* on the parsed figure?~~ **Answered with
   provenance instead of confidence** (§4.6): `salary_source` ('structured' | 'text' | 'llm') plus the
   as-printed `salary_raw` on every surface — the operator sees *where* a figure came from, and an
   LLM-mined band is badged; a numeric confidence would itself be an invented number.
4. **Per-source match budgets.** `match_daily_cap` is global. The fresh-vs-drain axis is handled — a
   `JOBCRAWLER_MATCH_DRAIN_FLOOR` slice is reserved so heavy new inflow can't starve the backlog drain
   (§4.7) — but *within* the fresh budget a noisy aggregator (RemoteOK) can still crowd out a high-signal
   single-company board. Do we want per-source or per-discipline sub-budgets, or a priority ordering of the
   pending drain by prefilter-survival strength rather than pure FIFO?
5. **State store.** Postgres (matches a scaled deploy) vs. SQLite (simpler) for v1 — SQLite ships; revisit at
   volume.
6. **Board list ownership & `enumerates_all` correctness.** A wrong `enumerates_all: true` on a windowed
   source would mass-close on every crawl (the §4.7 guard limits blast radius to non-empty crawls, but the
   diff is still wrong). Should the generator *detect and set* `enumerates_all` (e.g. by comparing crawl N vs
   N+1 coverage) rather than trusting the operator?
7. **Challenge backend & recheck cost.** Start with FlareSolverr or an in-process stealth browser? And does
   the §4.9 recheck loop's extra fetch budget (up to `recheck_cap`/pass) need its own politeness/solver
   accounting distinct from the crawl cadence?
8. **Metric calibration (a protocol, not guesses).** The §6 scalar's *combine form*, axis weights, and
   numeric thresholds (`min_items`, description chars, unique-residual floor, Jaccard, `ENUM_FLOOR_RATIO`) are
   *calibrated*, not asserted: A/B the combine form on the frozen held-out; sweep each threshold to maximize
   score spread between known-good and hand-broken (boilerplate-only / off-domain) fixtures, taking the robust
   *plateau*; seed weights from the `#e643b2` prior and regress against a downstream label (non-stale tenure).
   All thresholds stay host-side in the immutable TCB (§8). Match-rubric calibration is its own axis: does
   `MatchJobs`'s 4-band rubric agree with operator judgement on a labeled sample, and does a `PROMPT_VERSION`
   bump measurably move agreement?
9. **Typed-predictor round-trip** — *de-risked*: the optimized artifact is declarative **text** (§4.11); the
   pre-build toy milestone settles whether a *typed* `ChainOfThought` predictor round-trips end-to-end, else
   the instruction-string/CodeAct path is the default.
10. **Re-keying location consumers onto §4.12.** v1 deliberately leaves three readers on the flat
    surfaces: §17 role-group buckets and the T5 gate read `location_normalized`-else-raw (so a picker
    can show two buckets that RENDER identically — the display/grouping split-brain is accepted and
    pinned by test); the §4.7 prefilter's `exclude_locations` markers substring-match
    `normalize_location` output (PROFILE.md vocabulary would have to migrate with any change); and
    the §16 attainable gate reads only the §4.6 enums (a ledger-`worldwide` row whose `remote_scope`
    stayed `unspecified` doesn't qualify). Each re-key is mechanically easy — the atomic resweep makes
    the §17 cutover self-consistent, an ISO-fence arm would sharpen the prefilter, and an `OR
    worldwide-fence` arm would widen the shortlist — but each shifts operator-visible semantics
    (group membership, token spend, shortlist membership), so each is its own explicit sign-off,
    never a side effect of §4.12 landing.

## 15. Evidence base

Decisions above are grounded in the local research corpus (cited inline by short tag) **plus a live ATS
probe**. The optimizer/metric/security findings are unchanged from the news design (the mechanics are
domain-agnostic); the posting-domain facts are new.

**Live ATS field-availability probe (2026-07-02).** Direct requests to the major backends established the
§4.2 API-first strategy and its reconstruction gotchas:
- **Greenhouse** — `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`: whole board in one call,
  `meta.total` present; `content` full but **HTML-entity-escaped** (unescape then strip); no salary/employment;
  `location.name` flattened; `first_published` always.
- **Lever** — `api.lever.co/v0/postings/<token>?mode=json`: `description` is **intro only** (full =
  `description + lists[].content + additional`); `workplaceType` always; `categories.{location,team,commitment}`;
  `createdAt` epoch-ms; **no company field**.
- **Ashby** — `api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true`:
  `descriptionPlain/Html` full; `employmentType` always; structured compensation ~**70%**; `isRemote` = "remote
  *allowed*" (prefer `workplaceType`); payloads can be **MBs**.
- **Gupy (BR)** — `<company>.gupy.io` embeds all jobs in `__NEXT_DATA__`; `employability-portal.gupy.io/api/v1/jobs?companyId=..&limit=100&offset=..`
  paginates (limit ≤ 100; **don't trust `total` at limit=100 — page until short**); `type` enum
  (CLT/PJ/…); `publishedDate`+`expiresAt`; salary absent.
- **RemoteOK** — `/api` only latest ~**100** (partial → `enumerates_all:false`); `element[0]` a legal notice to
  **skip**; needs a **browser UA**; salary keys always **0**.
- **WeWorkRemotely** — per-**category** RSS complete (combined feed capped 10/category); `expires_at` present;
  `company` parsed from the `'Company: Title'` prefix.

**Research corpus (unchanged mechanics; full per-paper findings in the workflow notes):**
- **GEPA** (`#50c21d`) — separate `trainset`/`valset`, Pareto sampling, validation dominates budget,
  `minibatch≈3`, Merge model-sensitivity → §4.11.
- **"GEPA lifted a 1.2B model 25 pts"** (`#e643b2`) — the **saturation no-op**, graded reward + hard-case bias,
  `auto="light"`, per-example feedback, weighted-additive axis prior → §4.11, §6.
- **RLM ♥ GEPA** (`#286485`) — GEPA evolves an external *text* skill on an RLM; optimize-cheap/deploy-strong
  transfer; three-way held-out → §4.11.
- **HALO** (`#472657`, `#aa636b`) — aggregate trace decomposition is the gain source; per-stratum regression +
  noisy "peak"; capability-vs-harness triage → §4.11.
- **Meta-Harness** (`#c78a92`) — full traces ≫ scores-only, summaries no better than the scalar;
  small-hard-discriminative set → §4.11.
- **Darwin Gödel Machine** (`#4008a8`) — self-modifying agents reward-hack a *visible* evaluator → immutable
  TCB + hidden scorer + promotion margin > noise → §8, §4.11.
- **SRLM** (`#92bd81`) & **TRIPLE** (`#d550b5`) — K-sample selection *quality* (run sequentially) beats
  single-trajectory (+22%) → §6 selection.
- **Coding Agents are Long-Context Processors** (`#9b7ef3`) + **RLM** (`#ea21e9`) — code+environment substrate;
  addressable parts ≫ blob; recursion not the driver; JSON-literal injection (no pickle); FINAL channel →
  §4.2, §9.
- **MIPRO** (`#21498b`) + **CodeAdapt** (`#c4ad60`) — joint instruction+demo ≫ instruction-only; vacuous seeds
  underperform; exemplar selection is the cheap early lever → §4.11 ladder + seed.
- **Survey of Self-Evolving Agents** (`#b7bd66`) — offline/online taxonomy, tested rollback, quantified
  reward-hacking risk → §4.11, §8.
- **Metric design** (§6 scalar / §4.11 / §8) — additive/weighted-sum over multiplicative for collapsing
  quality axes, bounded sub-scores, gate-then-combine, calibration, over-optimization: **Netflix** (`#77d24b`),
  **retrieval-fusion** (`#dd2c1f`), **ReSeek** (`#fe1014`), **R1** (`#494c25`), **Sotopia-RL** (`#bdb383`),
  **iStar/over-optimization** (`#acb2cc`), **ShinkaEvolve** (`#f3ea65`), **Self-Attribution-Bias** (`#5622de`).
- Supporting: **LLM-in-Sandbox** (`#00c82a`), **CodeAct** (`#7a8470`), **Sanjaya** (`#745267`), **JudgeFlow**
  (`#e3bbdd`), **Feedback Descent** (`#244e30`), **TextGrad** (`#be34e3`), **gskill** (`#39b2ab`).

## 16. Web tracker (served in-process by `jobcrawl run`)

The crawler *finds* positions (§4.7); the tracker is where an operator *works* them — browse and search the
standing corpus, then move a posting through a personal application pipeline until it's applied, offered, or
dropped. It is a small **FastAPI + Jinja2** app (`web/`) **mounted in-process by `jobcrawl run`** — uvicorn
runs on the scheduler's own event loop, over the very same store instance, and a single SIGINT/SIGTERM
drains both. This realizes the "optional thin HTTP surface" §4.9 reserved, deliberately kept to the narrowest
possible role. (Running in-process — not a second `serve` process — is also what makes "crawl now" reliable:
the button's `request_crawl` is drained by *this* scheduler's `due_sites`, with no cross-process WAL lag and no
"UI up but no daemon" dead end. `--no-web` runs the scheduler headless.)

**The tracking axis (operator-owned, orthogonal to the crawl lifecycle).** The store's `open`/`closed` state
is the *crawler's* view (§4.7); the tracker adds a second axis that is entirely the operator's, defined
host-side in `tracking.py` and persisted opaquely by the store (§4.7 — "the store records state, never invents
policy"):

- **`track_status`** — a personal pipeline `new → interested → applied → interviewing → offer`, with terminal
  outcomes `accepted` / `rejected` / `withdrawn`. `tracking.py` owns the valid set, display order, labels, and
  colour tokens; `parse()` validates a submitted value **exactly** (no case-folding) before the store writes
  it, and rejects the unknown with the option list — a bad value is a 400 that writes nothing.
- **`applied_at`** — stamped **once**, the first time a status implies an application was sent
  (`implies_applied`: applied/interviewing/offer/accepted, but *not* rejected/withdrawn — you can bail before
  ever applying). The web layer decides the policy and passes `stamp_applied=`; the store stamps via
  `COALESCE(applied_at, ?)` so moving back down the funnel never rewrites the original date.
- **`dismissed`** (hide noise without closing — a dismiss is *not* a §4.7 close) and free-text **`notes`**.

These columns sit **outside** the crawler-supplied `fields` set, so a re-crawl, content change, or reopen
never clobbers them (§4.7). Per §8 this axis is **operator-facing** — no *crawler* generator/optimizer path reads
it, exactly like the match `reasons`; only the offline `jobcrawl match-trainset` projector may (to correct
**matcher** training labels).

**Surfaces.**

- **Browse / search** (`GET /`) — full-text query plus rich filters (site, company, lifecycle, `track_status`,
  workplace, level, min score, min salary, dismissed) and sort (score / recent / updated / salary / title —
  **default score desc**, so the listing leads with the best match and unscored rows sink last); dismissed rows are
  hidden by default, both lifecycle states shown (you still care about a posting you applied to after the board
  closes it). Paginated; every position links to its detail page and carries lifecycle + match-score pills.
- **Applications** (`GET /applications`) — the focused view of postings you've actually **applied to**
  (`search_positions(applied=True)` → `applied_at IS NOT NULL`, so a later `rejected`/`withdrawn` stays visible —
  the stamp is never cleared). A per-stage **funnel** across the applied→outcome stages (`applied_counts()`,
  counting only applied rows, so a role rejected *before* you ever applied never inflates it) tops a list sorted
  **newest-application-first** (`applied_at desc`; a `?stage=` chip filters to one stage), each row carrying the
  same inline stage select as browse so an application advances through the pipeline in place. Dismissed rows are
  kept — an applied role you later hid is still one you applied to.
- **Position detail** (`GET /positions/{pid}`) — the full posting (structured fields + verbatim description),
  a link to the original posting plus — when the crawler supplied a distinct `apply_url` (aggregators and most ATS
  boards store the listing page as `url` and the company's own application URL as `apply_url`) — a prominent
  **Apply** link (opened by the operator, never auto-actioned, §2), the "why this matched" reasons (§4.7,
  operator-facing), a **track panel** (status select, applied/updated/seen dates, dismiss/restore, and — for a
  crawlable board — a "crawl now" button), and a **notes** textarea. `pid` is urlsafe-base64 of `(site, url)`
  since the url can't be a path segment.
- **Dashboard** (`GET /dashboard`) — the pipeline **funnel** (counts per `track_status`), open/closed/matched
  tallies, the match-score distribution, per-site open counts, recent run health, and the **top-matches
  shortlist**: best effective fit first, at most one card per company (the §4.6 hiring-company key), and
  only rows attainable from anywhere — `remote_scope = 'anywhere'` or `relocation = 'offered'` (`matches
  (attainable_only=…)`); a fenced or unclassified posting never takes a slot however well it scores. The
  gate reads the §4.6 enums only; widening it with §4.12 fence data is §14 Q10.
- **Manual triggers** — "crawl now" calls `store.request_crawl(slug)` to flag a board for the daemon's
  `due_sites` to pick up (§4.9 single-owner: the UI must **not** run the crawl itself and overlap the board's
  own schedule); "rescore" parks open rows via `requeue_open_matches` for the §4.7 match-drain to flush (no
  crawl needed — the description is already persisted). Both return promptly; the daemon does the work.
- A small **JSON `/api`** mirrors the browse/detail reads for scripting; each mutation also has an HTML form
  path (POST → 303 redirect) so the UI degrades without JavaScript. `app.js` is progressive enhancement only
  (auto-submit a changed status select, keyboard triage, the detail slide-over); nothing requires it.
  Keyboard triage has two modes, both listed on the `?` help card: on the list, j/k move the highlight and
  enter/o/a/i/d/x act on it; with the **slide-over open** the same keys act on the SHOWN position — j/k
  (or ←/→) page through positions with the panel following, ↓/↑ scroll the focused panel, n jumps to
  notes, f opens the full page, and esc peels the topmost layer (help card → focused field → panel).

**Boundaries (what it must not do).** No scheduler, no LLM, no crawler import, no generated-code execution — it
only reads/writes the store and *flags* work, so it cannot widen the §4.4 sandbox surface or the §8 trust
boundary. Store handlers are plain `def` (Starlette threadpools them) so the blocking, lock-serialized
`sqlite3` calls never stall the event loop; the store's WAL + `busy_timeout` make the daemon-writer /
UI-occasional-writer overlap safe. **Profile editing is out of scope** for v1 (a `PROFILE.md` edit is still a
file edit the §4.7 drain picks up). Because it holds **personal application data**, `serve` binds `127.0.0.1`
by default (`JOBCRAWLER_SERVE_HOST` / `JOBCRAWLER_SERVE_PORT`, flag > env > config); `0.0.0.0` is opt-in and
only sound behind a trusted authenticating proxy (§8).

## 17. Cross-board duplicate clustering (read-derived overlay)

The same real-world opening is routinely stored several times — an ATS board row plus aggregator
copies (§14 Q1; measured at ~29% of open rows on the live corpus). **Rows are observations; identity
is derived.** `positions` keeps one row per `(site, url)` with its §4.7 lifecycle untouched; job
identity lives in two overlay tables (`position_identity_keys`, `position_clusters`) that a
deterministic host-side sweep (`runtime/dedup.py`, cadence `JOBCRAWLER_DEDUP_REFRESH_INTERVAL`,
default 15m, first pass on boot) recomputes from the rows alone (a third, `position_role_groups`,
rides the same sweep — presentation only, see *Role groups* below). **The overlay is disposable:**
dropping it and resweeping is lossless, every consumer is flag-gated (§11), identity state never
enters `content_hash`, and `cluster_id` is never persisted outside the overlay — so a bug in
identity rules can be reverted with an env var and can never corrupt crawl, closure, or tracking
state. The sweep swaps both tables in one transaction (readers see old-or-new, never empty), writes
its report to meta `dedup_last_report` (read by `jobcrawl dedup`), and is autonomy-*exempt*
bookkeeping — `JOBCRAWLER_AUTONOMY` brakes self-modification, and re-deriving state from unchanged
rules isn't that.

**Key tiers (a false merge is worse than a missed merge).** Keys for *every* tier are always
written — shadow measurement is free and the report reads it; `JOBCRAWLER_DEDUP_TIERS`
(default `0,1,2,3,6`) picks which tiers may form **edges**:

- **T0** identical canonical `url` across sites (re-canonicalized idempotently at derivation).
- **T1** ATS identity regex-extracted from `url` *and* `apply_url`: greenhouse (incl. `gh_jid` and
  embed `for`+`token`; ids must be ≥5 digits — the format guard), ashby (incl. `ashby_jid`), lever,
  smartrecruiters, workable, recruitee, workday (tenant-scoped org-qualified req ids only). An id
  observed under two orgs voids its edges (org-conflict veto) and is auto-blocklisted.
- **T2** normalized `apply_url` equality under the SEPARATE §17 param denylist
  (`JOBCRAWLER_DEDUP_APPLY_PARAM_DENYLIST`; identity-bearing params `gh_jid`/`ashby_jid`/`for`/
  `token`/`jk`/`pid`/`applyData` are never stripped; `urls.canonicalize_url` defaults and the PK URL
  form are untouched). Two key namespaces: `applyats:` (per-job ATS hosts — unconditionally
  edge-safe) vs generic `apply:` (needs sweep-time company+title corroboration and single-candidate
  anchoring; never trusted by the live between-sweep gates).
- **T3** `external_id` within a declared slug family only (e.g. the workingnomads tag feeds), never
  globally.
- **T4** cross-site `(company-key, content_hash)` — corroboration only, never edges; an ambiguous
  hash (two rows one side) is vetoed.
- **T5** `(company-key, geo-gated normalized title)` — built, **ships off** (measured hazards:
  robinhood's 9 identical-title reqs, seniority-stripped collisions); never within one site, never
  with seniority stripped. The shadow report is the evidence bar for ever enabling it.
- **T6** LinkedIn/Indeed posting ids (promoted to the default edge set after a zero-conflict audit).

Company-keyed tiers (T4/T5) are skipped entirely for sites whose company column is unreliable (HN
"Who is hiring") and for degenerate (<3 char) company keys. Two rows carrying **different** T1
identities can never be joined by any lower tier (the higher-tier separator), and a blocklisted key
(`jobcrawl dedup --block-key`, or the auto-veto) splits its group back to singletons — the safe
direction. Clusters are connected components (union-find, deterministic order); one member is
elected **canonical** per sweep: applied (earliest) > operator-touched > open > the company's own
board > longest description > `(first_seen, site, url)` — recomputed every sweep, so a closed
canonical is replaced as soon as a sibling is open.

**Ingest hook.** The §4.7 runner derives and replaces a row's keys inline on `new`/`changed`
outcomes (failure-isolated per row; failures count into the run's `signals.dedup_key_failures`,
never fail the crawl). Between sweeps, live sibling gates trust only edge-safe keys (T0/T1/T3/T6 +
`applyats:`) — generic `apply:`/T4/T5 wait for the sweep's corroboration.

**Consumers (each behind its own §11 flag, all ANDed with the master).**
- *Match inheritance* (`JOBCRAWLER_DEDUP_MATCH`): at the §4.7 cache-miss step, a candidate whose
  cluster (live keys ∪ materialized clusters) holds a fresh verdict — `matched_at` set, not
  `match_pending` (a profile edit parks every open row, so no stale-profile verdict is ever
  inherited), donor description at least as informative up to the 1200-char digest cap — inherits it
  row-level with `match_origin='cluster:<site>\n<url>'`. Free: draws nothing from the daily cap, and
  **never written to `match_cache`** (the cache stays a pure content→verdict map). Reversible in
  bulk: `jobcrawl dedup --requeue-inherited`. Inherited verdicts are excluded from the §8 matcher
  trainset (copies, not judgments — one LLM call must not vote N times).
- *Notify suppression* (`JOBCRAWLER_DEDUP_NOTIFY`): an append-only `notify_ledger` records per-row
  facts (`notified`/`dismissed`/`applied`); a `match` notification is suppressed when the row or any
  cluster sibling already has one — one ping per real job, enforced identically in the crawl sink
  and the drain sink. Seeded once at boot from existing strong matches (meta-guarded;
  `jobcrawl dedup --seed-notify-ledger` re-seeds at the current threshold).
- *UI collapse* (`JOBCRAWLER_DEDUP_UI`, §16): browse collapses a cluster to its canonical row
  ("also on N boards" chip; the detail page lists every sibling with its own score/status), and the
  default view hides rows whose cluster contains a dismissed or applied member — **dismissing one
  copy retires the job everywhere at read time, without writing to siblings**. "New since" counts
  distinct jobs, and the dashboard shows distinct-open over raw-open. Queries filtered on the
  operator's own data (applied, dismissed, an operator-set `track_status` — the default `'new'`
  stage is exempt) always bypass collapse; `?collapse=0` (the "Everything" pill) is the per-request
  raw escape; every `pid` keeps resolving regardless.

**Role groups (presentation-only, `JOBCRAWLER_DEDUP_ROLE_GROUPS`).** One company opening one role
in several locations is the inverse hazard: real distinct postings (separate reqs, separate apply
forms) that must never cluster — exactly T5's measured false merges — yet read as duplicates in the
list. The sweep bundles them in a third disposable table (`position_role_groups`, swapped in the
same transaction; stays empty while the knob is off) keyed on `(company key, normalized title key)`
over exactly what the collapsed list shows (open cluster-canonicals + open standalone rows — never
a shadowed duplicate, which would double-count the role), requiring ≥2 members over ≥2 distinct
case-folded locations (same-city multiples — robinhood's nine identical reqs — and all-blank
locations never group) and skipping keys spanning >64 rows (`role_group_cap_skips` guard:
mass-poster boilerplate titling, not one role in 64 cities). The location bucket reads
`location_normalized`-else-raw, lower-cased — NEVER the §4.12 `location_display` (a label there,
not a key; two buckets can render identically, and re-keying is §14 Q10). The browse list shows one row per
group behind the same `?collapse=0` escape; the primary is elected at **query** time — highest
rounded effective score, then earliest `first_seen`, then `(site, url)`; closed rows never role-
collapse — so a verdict landing between sweeps resurfaces the better variant immediately. The
surviving row wears an "N locations ▾" picker (also opened by the row's apply/visit click, so the
operator chooses a location before leaving) linking every open variant; the detail page lists the
other locations with their own scores. Because members are real separate postings, the overlay is
read by the UI **only**: matching, notify, lifecycle, and tracking never touch it, and
operator-data queries bypass it exactly like cluster collapse.

**Boundaries (what it must not do).** Never delete, merge, or rewrite a `positions` row. Never touch
`status`/`missing_streak`/`closed_at` — closure stays per-board per §4.7, and one board closing
never closes a sibling. Never let derived identity into `content_hash`. Never block or fail a crawl.
Never propagate operator columns by write. Never distort §4.8 health signals or §6 gates (per-board
counters stay raw). **Never call an LLM (§3)** — keys are pure functions of stored fields; the only
LLM the pipeline runs remains the §4.7 matcher, which §17 makes *rarer*, never different. All
thresholds, families, allowlists, and the blocklist live host-side (§8), never authored or visible
to generated code.

## 18. Auto-apply prefill (operator-triggered form filling in the operator's Chrome)

**What it is.** Every position whose apply target is an http(s) URL (per-job `apply_url` when the
crawler captured one, else the listing URL; `mailto:` targets are out — the resolver is
`state.autofill_target()`, kept in `state.py` so the web layer never imports the dspy/CDP-heavy
worker) gets a detail-page button: *Pre-fill application in Chrome*. Pressing it opens the apply
page as a new tab in the operator's own logged-in Chrome (CDP over the `chrome://inspect` remote-
debugging port, default 9222) and an LLM loop fills the form from `PROFILE.md` — text fields,
selects, checkboxes, radio groups, the CV file upload — then leaves the tab open for the operator
to review and submit. **It never submits**, and the guarantee is mechanical, not prompted (below).

**Boundary (§16 pattern).** The web process only enqueues: the button's POST writes a row to
`autofill_runs` (`status='queued'`; idempotent per position while a run is queued/running) and
redirects. The daemon drains the queue on its **own asyncio task** (the §4.7-drain lesson: a
generation-heavy tick must not starve it), claiming the oldest row (`queued→running`, atomic),
driving the browser, and finishing it `done`/`failed`. The poll (default 3 s,
`JOBCRAWLER_AUTOFILL_POLL`) *is* the cadence — no due/mark timer — and the inner loop drains the
backlog back-to-back before sleeping again. Operator-initiated work is autonomy-exempt (like
"crawl now"). One long-lived CDP connection is kept across runs so Chrome's "Allow debugging"
consent fires once per daemon lifetime; a daemon killed mid-run leaves its row `running`, and a
boot sweep (`reset_stale_autofill`, the autofill `reap_orphans`) fails those so the position's
idempotent re-request isn't wedged.

**The loop (worker = `autofill.AutofillWorker`, CDP client = `cdp.CdpBrowser` on aiohttp).** Per
round (max `JOBCRAWLER_AUTOFILL_ROUNDS`, default 6): a JS sweep walks the DOM (main document +
same-origin iframes; cross-origin frames are counted, not entered) and builds a digest of form
controls and buttons — each element stashed in `window.__jcAF` under an opaque per-round ref id.
The LLM (dspy `Predict(AutofillStep)`, `JOBCRAWLER_AUTOFILL_MODEL` else the match model,
temperature 0) sees profile + position context + digest and answers a JSON action list:
`fill`/`select`/`check`/`click`/`upload`/`done`. Executors resolve refs against `__jcAF` — the
model never writes JS or selectors. A `done` that follows a successful `click` in the same round
is **deferred**, not honored — the model is declaring done for a page it hasn't seen (the
BambooHR-modal / WWR→ATS-hop zero-fill bug); the loop re-sweeps and plans one more round. The
click path also stays in the driven tab: anchor `target=_blank` is rewritten to `_self` before
clicking, and when page JS opens a popup anyway (`window.open` apply flows) the worker adopts it —
`Target.getTargets`, restricted to targets whose `openerId` is one of the run's own tabs, so an
operator's unrelated tabs are never grabbed. After any click round it settles
(`_POST_CLICK_SETTLE_S`, 2 s — SPA modals render past `readyState=complete`) before re-reading.
Navigation resets the world, so every round re-sweeps; fills
use the native value setter + `input`/`change` events (no keystrokes, so no accidental Enter-
submit); `upload` attaches only the operator-configured `JOBCRAWLER_AUTOFILL_RESUME` path
(default `CV.pdf` — the repo-root convention beside `PROFILE.md`; upload reports unavailable
when the file is absent) via `DOM.setFileInputFiles`. Optional `APPLY.md`
(`JOBCRAWLER_AUTOFILL_NOTES`) rides along with the profile for form-only facts (phone, links,
salary expectation, notice period).

**The no-submit guard (mechanical).** The `click` executor — the only executor that can activate
a control — refuses, in shared Python/JS pattern source (`_words_pattern`; text NFD-folded,
accents stripped, lowercased): (a) submit-worded controls anywhere (*submit, enviar, finalizar
candidatura, send application, …*); (b) submit-**typed** controls (`type=submit|image`, including
a form-embedded `<button>` with no type attribute, which defaults to submit) inside a `<form>`;
(c) wizard-advance words (*next, continue, continuar, próximo, avançar, …*) inside a form —
multi-step ATS wizards often validate-and-advance-and-submit. "Apply"/"candidatar" words are
deliberately **not** blocked: on Lever/Ashby/Gupy they reveal the form and must stay clickable;
"enviar" catches the actual final ("Enviar candidatura"). There is no raw-JS action, no Enter
keystroke, and no way for the model to name a file path.

**Self-improvement log (the §4.7 `generation_log` pattern).** `autofill_runs` is queue *and*
audit log: every round's page digest, raw LLM output, and per-action results land in
`transcript_json`, with `model`, `profile_hash`, `rounds`, `actions_applied`, `fields_filled`,
and a human `summary`/`error` on the row. Reads for the detail panel exclude the transcript
column (it can be hundreds of KB); finished rows are pruned behind a retain window (200),
queued/running rows never. Knobs: `JOBCRAWLER_AUTOFILL` (default on; worker construction fails
soft to "off" when no model is configured), `_MODEL`, `_TOKENS` (default 6000), `_ROUNDS`,
`_POLL`, `_CDP` (default `http://127.0.0.1:9222`), `_RESUME`, `_NOTES`.
