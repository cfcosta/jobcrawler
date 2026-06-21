# bizup-autocrawler — SPEC

| | |
|---|---|
| **Status** | Draft |
| **Date** | 2026-06-20 |
| **Depends on** | `bizup-news` (sibling service; ingestion target) |
| **Core tech** | `dspy.RLM` generation + `GEPA` self-improvement (DSPy 3.2.x) |

## 1. Summary

`bizup-autocrawler` turns a **list of websites** into a fleet of **per-site crawler
scripts** that continuously harvest news items and feed them to the `bizup-news`
service. A `dspy.RLM` agent does the hard reasoning **once per site** — it explores a
prefetched snapshot of the site in a sandboxed REPL (fetching more on demand via host
tools), finds the cleanest way to discover articles, and **writes a standalone Python
crawler script** for that site. Thereafter the script
runs on a schedule with **no LLM in the loop**, yielding `{title, body, url}` items
that are deduplicated and `POST`ed to the news API.

This mirrors the news service's own design principle — *the LLM runs at ingestion
only; the hot path never calls a model.* Here: **the LLM runs at generation only;
a crawl never calls a model.** The generator itself **improves over time** through an
offline optimization loop (§4.11), kept strictly off the crawl path. (Ingestion itself is *not* free,
though: each `POST /news` drives 2–3 server-side LLM calls in `bizup-news` — budgeted in §4.7.)

## 2. Goals / Non-goals

**Goals (v1)**
- Given a site URL, automatically generate a committed, reviewable `crawlers/<slug>.py`.
- Prefer structured discovery (RSS/Atom/JSON feeds, sitemaps); fall back to HTML scraping.
- Support both static fetch (httpx) and headless rendering (Playwright) per site.
- **Own the run loop**: a built-in scheduler decides when each site is due and runs its script (bounded concurrency, retry/backoff), then dedups and ingests into `bizup-news` via `POST /news`.
- Detect when a script has gone stale (site changed) and **regenerate** it.

**Non-goals (v1)**
- Logins, paywalls, **interactive** CAPTCHA solving (Turnstile/hCaptcha), and proxy-rotation evasion. Passive Cloudflare JS challenges *are* handled transparently (§4.10).
- Near-duplicate clustering / cross-site dedup (the news service owns semantic work).
- Translation, media/asset downloading, comment harvesting.
- A general-purpose crawler config UI. The site list is a checked-in file.

## 3. Core principle: two phases, one LLM phase

```
            ┌─────────────────────── GENERATION (LLM, once per site) ──────────────────────┐
 site_url ─▶│  dspy.RLM root planner                                                        │
            │    explores via host tools:  fetch() · render() · find_feeds() · get_sitemap()│
            │    drafts crawler source, then validate_crawler() runs it for real & reports  │
            │    iterates until items look right ─▶ SUBMIT(crawler_source)                   │
            └───────────────────────────────────┬──────────────────────────────────────────┘
                                                 ▼
                                   crawlers/<slug>.py  (committed, reviewed)
                                                 │
            ┌────────────────────────────────────┼─────── CRAWL (no LLM, scheduled) ────────┐
            │  runner imports module ─▶ crawl(ctx) yields Item(s)                            │
            │    ctx enforces robots · rate-limit · timeouts · allowed-hosts (script can't   │
            │    bypass)  ─▶ normalize ─▶ dedup vs crawl-state ─▶ POST /news {title, body}   │
            └───────────────────────────────────────────────────────────────────────────────┘
                                                 │
                              health signals (item counts, errors) ─▶ stale? ─▶ re-enter GENERATION
```

The **generated script declares _what_ to fetch and _how_ to extract**; the **runner
owns _politeness and limits_ centrally**. A generated script therefore cannot hammer
a site or reach the network except through the supplied `ctx`.

The crawl phase is **driven by a built-in scheduler** (§4.9), not an external cron:
`bizup-autocrawler` runs as a long-running service that decides when each site is due
and operates the loop itself — generation, crawling, and regeneration under one orchestrator.

## 4. Components

### 4.1 Site registry (input)
A checked-in list (`sites.yaml`) is the system's input. One entry per site:

| field | type | notes |
|---|---|---|
| `slug` | str | stable id; names `crawlers/<slug>.py` and the source field on items |
| `name` | str | human label |
| `url` | str | site root or section URL |
| `render` | `auto \| static \| browser \| solver` | **max** fetch tier `ctx` may escalate to (§4.10); `auto` lets generation decide |
| `hints` | str? | optional free-text steer for the generator ("articles live under /news") |
| `schedule` | str? | crawl cadence — `30m`, `6h`, or a cron expr; defaults to the global cadence |
| `enabled` | bool | crawl on/off without deleting |

### 4.2 Generator (the RLM phase)

**Module choice.** The generator writes and *runs* Python iteratively, so it needs a
code-execution loop, not just discrete tool calls. `dspy.RLM` fits: a news homepage is
large, messy HTML best explored *with code + recursive sub-LLM calls* in a persistent REPL
— RLM's core strength. The caveat to keep honest: an agent-driving-external-tools loop is
normally `dspy.ReAct`/`dspy.CodeAct` territory, so we keep RLM **idiomatic** by handing it a
pre-fetched site snapshot as the large input it reasons over in-REPL, using host tools only
for *follow-up* fetches. RLM stays the **primary** generator; for self-improvement (§4.11) we
do *not* invert to CodeAct but wrap the RLM in a `CrawlerGenerator(dspy.Module)` and point the
optimizer at a named predictor we own. `dspy.CodeAct` ("writes Python that calls your tools")
remains the documented fallback if that predictor optimizes unevenly.

**RLM vs CodeAct is empirically close, not RLM-obviously-better.** The load-bearing property is
*write → run → iterate code over an environment*, which both satisfy; head-to-head, coding agents
match or beat RLM on single-document tasks, recursion adds only ~6 pts (sometimes *negative* on
strong frontier models), and RLM is *worse* than a plain call below a ~2¹⁴-token crossover
[Coding-Agents, SRLM, RLM]. So: **gate recursion on snapshot size** (prefer the non-recursive /
CodeAct path when the snapshot fits one context — the common homepage case; reserve recursive
`llm_query` for genuinely large corpora like huge sitemaps/archives); add a **feed-clean fast-path**
that skips DOM exploration/recursion when `find_feeds`/`get_sitemap` already return a clean feed (it still
runs *one* `validate_crawler` pass before `SUBMIT`, satisfying gate-2 must-observe — sandbox-only, no
LLM); keep the owned
strategy predictor (§4.11) **module-agnostic** so an RLM→CodeAct swap doesn't invalidate it; and add
a production-backbone **A/B** (RLM-recursive vs non-recursive CodeAct) on the held-out split in
M1/M5, keeping the per-backbone winner. Define the fallback trigger *operationally*: the GEPA
candidate fails to beat the incumbent across K budgets, or per-gate pass-rates regress on a subset.
Keep **depth-1** recursion (sub-calls are leaf LMs; host tools are the follow-up channel) and async.

A DSPy signature whose **docstring is the task instruction** (per project convention),
driven by `dspy.RLM`. Illustrative shape (not implementation):

```python
class GenerateCrawler(dspy.Signature):
    """Write a standalone Python crawler for one news site.

    Explore the prefetched snapshot; fetch more with the tools only if needed.
    Prefer a feed or sitemap; scrape HTML only if neither exists. Emit a module
    implementing the crawler contract (§5). Validate it with validate_crawler
    before submitting; it must yield several items, each with a non-empty title,
    body, and URL."""

    site_url: str          = dspy.InputField(desc="site root / section URL")
    corpus: dict           = dspy.InputField(desc="prefetched {homepage, robots, sitemap, articles:[{url,title,text}]} — JSON-able")
    render: str            = dspy.InputField(desc="static | browser | solver | auto")
    hints: str             = dspy.InputField(desc="optional operator guidance")
    discovery_method: Literal["feed", "sitemap", "scrape"] = dspy.OutputField(desc="how articles are discovered")
    discovery_url: str     = dspy.OutputField(desc="the feed / sitemap / index URL used")
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
these handle *follow-up* fetches — pagination, a deeper article, a discovered feed:

| tool | purpose |
|---|---|
| `fetch(url)` | static GET → cleaned text/markdown (httpx + markitdown), rate-limited |
| `render(url)` | headless GET → rendered HTML (Playwright) |
| `find_feeds(url)` | discover `<link rel="alternate">` RSS/Atom/JSON feeds |
| `get_sitemap(url)` | fetch & parse `sitemap.xml` (and robots-declared sitemaps) |
| `validate_crawler(source)` | fence-strip, then run the candidate in the execution sandbox (§4.4) **through `ctx`** — against the live site during generation, or in the offline loop against the persisted `corpus` + recorded HTTP via a replay-backed `ctx` (§4.11), *never the network*. Returns a **size-bounded** `CandidateResult` (capped `samples`, condensed `schema_errors`, `exception` = type+message+traceback *tail*) so it survives the REPL's 10k head+tail truncation, crossing the bridge as JSON the model parses in-REPL. |

`validate_crawler` runs the candidate **on the host**, and that's mandatory, not just
convenient: RLM's Pyodide sandbox has no network and none of the crawler's libraries
(`httpx`/`lxml`/`selectolax`/`playwright`), so the host execution sandbox (§4.4) is the only
place a generated crawler can actually run. The model iterates write → `validate_crawler` →
fix before `SUBMIT`.

**LMs & budgets.** A strong code-capable **root LM** plans and extracts; a cheap **`sub_lm`**
answers in-sandbox `llm_query` calls — pass it explicitly as a concrete LM (an unset `sub_lm`
silently falls back to the *root* LM, billing every `llm_query` at planner rates). Scope the LM
per call with `with dspy.context(lm=root_lm): await gen.acall(...)`, never a process-global
`dspy.configure` (it is thread-owned and raises from the scheduler's worker tasks — §9). Build
the generation LM with `cache=False` so each (re)generation re-reasons and `BestOfN`/`Refine`/GEPA
sample distinct candidates. Set both RLM caps explicitly (`AUTOCRAWLER_MAX_ITERATIONS`,
`AUTOCRAWLER_MAX_LLM_CALLS`): `max_llm_calls` raises a *recoverable* in-sandbox error at its limit
(the model can catch it and keep going), so it is a **soft** cap, not a hard runtime/spend bound —
pair a bounded `max_iterations` with the host-side ceilings below, especially under
`BestOfN`/`Refine`/GEPA multiplication. The default sandbox needs **Deno** installed.

**Hard cost ceilings (host-side backstops).** The installed RLM is bounded *both* ways — the root loop
is `for _ in range(max_iterations)` (default 20) with an extract-fallback on exhaustion, and
`max_llm_calls` caps sub-calls — so `AUTOCRAWLER_MAX_ITERATIONS`/`MAX_LLM_CALLS` are pass-through values
to `dspy.RLM(...)`, not separate host caps. Two real gaps remain: `max_llm_calls` is **soft** (a
recoverable error the model can catch and loop past, so it doesn't *hard*-bound sub-calls), and
**fan-out** multiplies whole generations, which no single RLM cap bounds. So the host adds an
**unconditional** sub-call kill (`AUTOCRAWLER_MAX_SUBCALLS`, set ≥ `max_llm_calls`) that aborts the
trajectory regardless — the *only* hard stop on a catch-and-continue sub-call loop — and an aggregate
`FANOUT_SPEND_CAP` across the fan-out; plus a one-line anti-over-subcall prompt guard. (Size the $ cap
against the **p95 tail**, not the mean.) The Pyodide/Deno sandbox adds ~2–3 s cold-start + per-call
bridge tax per loop [Sanjaya] — confirm RLM reuses one warm sandbox per generation, else fold that into
the cap.

**Output handling:** `crawler_source` is written to `crawlers/<slug>.py` (and, by
default, surfaced for human review — see §8) only after it passes the §6 acceptance gates.

### 4.3 Crawler script contract (the generated artifact)
Every generated module exposes a fixed interface so one runner can execute any of them:

```python
SITE = "https://example.com"          # module-level constant

def crawl(ctx) -> Iterable[Item]:     # yields normalized items; no network except via ctx
    ...
```

- `ctx` provides `ctx.fetch(url)`, `ctx.render(url)`, `ctx.get(url, **kw)`, `ctx.log(...)`.
  **All fetching goes through `ctx`** so limits are enforced (parsing is the module's own — via the
  allowlist below — not a `ctx` concern).
- The module may `import` only from an allowlist (parsers like `selectolax`/`lxml`,
  `feedparser`, stdlib `re`/`json`/`datetime`/`urllib.parse`). No `os`, `subprocess`,
  `open`, `socket`, `eval`/`exec` (enforced by AST check, §8).
- **Body extraction is the crawler's job**, via the allowlisted parsers with per-site selectors (their
  brittleness is what §4.8 drift-detection + regeneration handles). The `fetch` tool's markitdown is
  whole-page cleanup for the *generator's* exploration, **not** an article-body extractor; an optional
  `ctx.extract_article(html)` (trafilatura/readability) may back it later.

### 4.4 Runner & execution sandbox
- Imports `crawlers/<slug>.py`, calls `crawl(ctx)`, and consumes the yielded items.
- Runs the module under resource limits: wall-clock timeout, max pages, memory cap,
  no filesystem writes, network egress **only** through the `ctx` proxy.
- A failing script (exception, zero items, schema violation) is isolated to its site and
  recorded as a health signal — it never aborts the whole crawl.
- `validate_crawler` and committed crawlers run in a **single pinned, reused** general-purpose sandbox
  image (Python + httpx/lxml/selectolax/feedparser/playwright), not a per-site image, with a soft
  ~10 s timeout + hard kill and **no network except the `ctx` egress-allowlist** — container isolation
  alone is necessary-but-not-sufficient when exploring untrusted site content (§8). Size `BestOfN`
  width + offline fixture re-eval against host RAM (~200 MB/container); acceptance is LLM-bound, not
  sandbox-bound [LLM-in-Sandbox], so the budget knob is RLM call count, not container count.

### 4.5 Politeness & fetching (`ctx`, shared by all crawls)
Centralized so generated scripts can't get it wrong: `robots.txt` compliance, per-host
rate limiting + concurrency caps, retries with backoff, conditional GET (ETag /
Last-Modified), request timeouts, and an identifying `User-Agent`. `ctx.fetch`/`render`
restrict requests to the site's host(s) and on-domain article links (SSRF guard, §8). On a
detected Cloudflare challenge, `ctx.fetch` transparently escalates to the browser/solver tier
and caches the clearance cookie (§4.10) — scripts stay oblivious. `ctx.fetch`/`render` also **resolve
text encoding** (HTTP charset → meta charset/BOM → `charset-normalizer` fallback) and hand the crawler
UTF-8 `str`, so per-site code never guesses charset.

### 4.6 Normalization & `Item` model
Items yielded by scripts are validated against a Pydantic model:

| field | type | required | use |
|---|---|---|---|
| `url` | str | ✓ | canonicalized; the dedup key |
| `title` | str | ✓ | → news `title` |
| `body` | str | ✓ | → news `body` |
| `published_at` | datetime? | – | metadata / ordering |
| `source` | str | ✓ | the site `slug` |

`POST /news` currently accepts only `{title, body}`, so v1 sends exactly that and keeps
`url`/`published_at`/`source` in crawler state. (Follow-up §13: extend the news API with
`url` + an idempotency key so re-ingestion is safe server-side.)

**URL canonicalization** is one shared runtime helper used by the dedup key (§4.7), the gate-3 Jaccard
(§6), and — via its host-only primitive — the SSRF on-domain check (§8): lowercase scheme+host, strip
default port, drop fragment, drop tracking params (`AUTOCRAWLER_TRACKING_PARAM_DENYLIST` — `utm_*`,
`fbclid`, …), sort remaining query params, normalize trailing slash + percent-encoding case. It is
**load-bearing**: two runs differing only on `?utm_source=…` must not lower the Jaccard, fail gate-3,
and get a correct crawler rejected. Do **not** auto-honor an attacker-controlled `<link rel=canonical>`
for the dedup key in v1.

### 4.7 Dedup & crawl state
The autocrawler owns its own small state store (decoupled from the news DB): a set of
seen canonical URLs (and/or content hashes) per site, plus per-run health counters.
Postgres fits the existing stack; SQLite is acceptable for v1. Two per-item states: **seen** (set at
dedup; prevents re-extraction) and **ingested** (set only after a 2xx from `POST /news`). Each POST is
**not free** — it drives 2–3 server-side LLM calls (summarize + classify, with one classify retry) and
`bizup-news` has **no server-side idempotency** (bare INSERT) — so two ingest-specific controls: (a) cap
a *first* crawl of a large feed to the **newest-K** items (`AUTOCRAWLER_FIRST_CRAWL_CAP`) and rate
ingestion with a throughput **governor** sized to news-API LLM capacity (`AUTOCRAWLER_INGEST_RATE`),
*distinct* from the §4.5 fetch-politeness cap; (b) make re-attempt **failure-class-aware** — auto-re-POST
only on unambiguous *pre-insert* failures (conn-refused / DNS / 4xx); treat 5xx / timeout-after-send as
**terminal-for-cycle** (surface via §4.8 `errors`), because a duplicate send there is duplicate *LLM
spend*, not just a duplicate row. Ingest **per-item** (not batched). `seen`-vs-`ingested` is the
at-most-once mitigation; the residual double-send shrinks once the §14 Q1 idempotency key lands (don't
pull that `bizup-news` change into v1 scope).

### 4.8 Health & self-healing regeneration
Each crawl records `items_found`, `new_items`, `errors`, `pages_fetched` per site. A site is flagged **stale** on a *single* zero-**`items_found`** run (never zero `new_items` — a healthy
low-frequency feed legitimately adds nothing) against a baseline that previously yielded items, confirmed
by a short re-probe that **re-parses ≥1 item** (not merely network-OK). One re-probe-cleared blip is
tolerated. (A separate error-rate trigger is deferred post-v1 — zero-`items_found` catches the dominant
failure; the `errors` counter is recorded either way.) Stale sites are re-entered into §4.2 generation; the new script is
validated and proposed to replace the old one (review gate per §8). This is the
"regenerate when the site changes" loop made automatic.

Regeneration runs the **current** generator. If generation exhausts its budget without a
gate-passing crawler — including a forced RLM extraction (`final_reasoning == "Extract forced
final output"`, §4.2) — the site enters a distinct **generation-failed** state, surfaced
separately in `autocrawl list` and *not* silently re-enqueued into the same failing generation
(which would spin). Improving the generator itself is the separate offline loop in §4.11.

Track **drift-MTTR** — median time from a previously-healthy site going dark to a gate-passing
replacement being *enabled* (detection + regeneration + review gate) — as an **observability metric**
(the §14 Q2 review-gate tier trades against it). It is *not* an M3 exit gate: gating M3's close on a
median-over-drift-events would depend on enough organic drift events M3 must first produce — a
bootstrapping circularity.

### 4.9 Scheduler / orchestrator (owns the run loop)
`bizup-autocrawler` runs as a **long-running service** — `autocrawl run` starts an async
scheduler that operates the whole crawl loop itself, rather than relying on an external cron.

- **Due-selection.** Each site has a cadence (`schedule` in §4.1, else `AUTOCRAWLER_DEFAULT_SCHEDULE`).
  The scheduler keeps `next_run` per site in the state store and dispatches sites whose time has come.
- **Bounded concurrency.** A global cap (`AUTOCRAWLER_CONCURRENCY`) limits how many sites crawl at
  once; per-host limits come from the politeness layer (§4.5). A site never overlaps its own prior run.
- **Retry & backoff (one owner per failure class).** A crawl that fails *before yielding/ingesting any
  item* gets a small bounded backoff+jitter retry **here**; **transient fetch** errors belong to the
  politeness layer (§4.5); **ingest/POST** failures are §4.7's next-crawl re-attempt and are *not*
  scheduler-retried (no re-fetching a whole site because the news API 5xx'd on one item). Persistent
  failure feeds the §4.8 health signal instead of stalling the queue.
- **Regeneration trigger.** On a timer (`AUTOCRAWLER_REGEN_CHECK`) the scheduler evaluates staleness and
  enqueues stale sites for RLM regeneration (§4.8) — one orchestrator drives both crawling and generation.
- **Re-optimization hand-off (§4.11).** A slow timer (`AUTOCRAWLER_REOPT_CHECK`) or a trainset-growth
  threshold enqueues the **offline** self-improvement job — but the scheduler never runs `.compile()`
  in-band (a multi-hour GEPA run, hundreds of metric calls each spinning a fresh RLM sandbox, would
  violate the never-stall guarantee). It hands off to an out-of-band job and later adopts the new
  versioned generator program.
- **Run history.** Each run records the canonical Run record (§5) — `{site, started, finished,
  items_found, new_items, errors, pages_fetched}` — for observability and the staleness math.
- **Lifecycle.** Graceful shutdown drains in-flight crawls; state lives in the store, so the loop is
  crash-safe and restart-idempotent.

v1 uses an **in-process async scheduler** (no external broker) — appropriate for a checked-in list of
news sites; a task queue is a later scale-up (§14). An optional thin HTTP surface (status, manual
trigger) can mirror `bizup-news`'s FastAPI style if operators want one.

### 4.10 Cloudflare / anti-bot challenge handling
Many news sites sit behind Cloudflare, but most use it only as a CDN (plain `httpx` works), and
**feeds-first discovery** (§4.1) often sidesteps challenges since RSS/sitemap endpoints are frequently
unprotected — but *how* often is a **measured fact**, not an assumption: the **M1 step-0** feed/sitemap/
challenge tally (§13) sizes how much architecture weight rides on these tiers (cross-ref §14 Q6). When a
real challenge appears, `ctx` handles it **transparently** —
generated scripts never contain anti-bot logic.

- **Detection.** A response is flagged on known challenge signals (`403/503` with
  `cf-mitigated: challenge`, or `Server: cloudflare` plus interstitial/Turnstile markers).
- **Escalation ladder.** `ctx.fetch` escalates *only on detection*: **static httpx → headless
  browser → challenge solver**, bounded by the site's `render` ceiling (§4.1). No challenge → no
  escalation, so the common case stays cheap.
- **Plain Playwright/Selenium is not enough.** Vanilla automation is fingerprinted and fails CF's
  JS challenge, so the solver tier uses a **stealth** browser.
- **`ChallengeSolver` interface (pluggable).** `solve(url) -> {cookies, user_agent}`. Backends:
  - **In-process stealth browser** (nodriver / camoufox / patchright) — integrates with the
    Playwright tier, no extra service. *Recommended primary.*
  - **FlareSolverr** — an HTTP service driving a stealth browser, returns `cf_clearance`. Least
    code to adopt and language-agnostic, but historically lags CF updates and is resource-heavy.
    Reasonable first backend; swap if it underperforms.
  - **Paid CAPTCHA solver** (2captcha/capsolver) for interactive Turnstile — optional, off by
    default (cost + ToS); see non-goals.
- **Clearance cache ("pay once").** A solved `cf_clearance` + its User-Agent are cached per host
  and replayed with cheap `httpx` until expiry — the generate-once / run-cheap pattern again.
  **Constraint:** `cf_clearance` is bound to **IP + UA**, so the solver and the subsequent cheap
  requests must share egress IP, User-Agent, and proxy.
- **Budget & give-up.** The solver tier is slow and concurrency-capped; a site needing an
  interactive CAPTCHA on every request is marked **unsupported** rather than retried forever.

### 4.11 Self-improving generator (offline optimization loop)
The generator is **not static** — it improves over time by optimizing the instruction it
generates from. This is a **core capability**, but deliberately **offline, batched, and
out-of-band**: it never runs on the crawl path or inside the §4.9 scheduler's in-band work.
Two loops, never conflated:

- **Online regeneration (§4.8)** invokes the *current, pinned* generator and stays in the scheduler.
- **Offline self-improvement (here)** runs an optimizer over accumulated examples and produces a
  *new, versioned* generator program for the regeneration loop to adopt.

**The generator is a `dspy.Module`.** Wrap the `dspy.RLM` (and the §6 selection loop) in
`CrawlerGenerator(dspy.Module)`; sub-modules held as attributes auto-register as parameters, making them
visible to optimizers and to `save`/`load`. **Async caveat:** `BestOfN`/`Refine` (and the custom
selection loop over them) implement only sync `forward` (no `aforward`), and `Module.acall` awaits
`aforward` with no sync fallback — so `CrawlerGenerator.aforward` must invoke the wrapper's **sync**
surface offloaded via `asyncify`/`run_in_executor`, never delegate into a missing wrapper `aforward`,
else it raises `AttributeError` and/or blocks the §4.9 single-process scheduler. RLM stays primary; we do
**not** invert to CodeAct.

**What GEPA optimizes.** RLM *does* expose optimizer-targetable predictors (`generate_action`, `extract`,
both `dspy.Predict`, enumerable via `named_predictors()`), but optimizing them mutates RLM's **generic
action loop** — a broad, model-sensitive blast radius that collides with the §8 immutable TCB. So by
**design choice** (not API necessity) GEPA is scoped to a *narrow* predictor we own — an extracted
`dspy.ChainOfThought` *strategy* sub-module (`site_features -> discovery_plan`) the RLM/CodeAct loop
executes — for blast-radius control [Darwin-Gödel-Machine]. GEPA rewrites that predictor's
docstring-instruction. `dspy.CodeAct` is the fallback if the owned predictor optimizes unevenly. (§14:
the toy milestone may also probe whether GEPA can usefully optimize `generate_action` directly without
breaching §8.)

Two load-bearing requirements on this predictor. **Seed it** with a hand-authored, *ordered-rule*
docstring enumerating the discovery decisions (probe `find_feeds`/`get_sitemap` before DOM scraping;
feed > sitemap-freshness > HTML-heuristic; boilerplate exclusion; "verify ≥N items before
finalizing") — optimizers can't infer a rule set from a vacuous seed [MIPRO]. And define
`site_features` (the predictor's **input**) to carry the per-site facts GEPA must *not* memorize
(discovered feed/sitemap URLs, HTML outline), forcing it to evolve *generalizable* strategy rather
than hard-coding fixture feed-URLs — GEPA's meta-prompt deliberately bakes domain facts into the
instruction, so without this it overfits the trainset's site mix and **destroys the cold-start
payoff** [GEPA, gskill]. Treat field descriptions as part of the optimizable surface.

**One scoring core, three adapters.** §6's **gates 1–2** are computed by a single pure scoring core:
one `validate_crawler` run → `CandidateResult` + a graded float score + a `gate_failures` string. It
runs the candidate through `ctx`; the **offline** metric injects a replay-backed `ctx` (persisted
`corpus` + recorded HTTP) so it never hits the network. The **gate-3** stability verdict is computed by
the harness comparing *two* core invocations and **appended to the GEPA feedback text** (like the
across-run aggregates below), never entering the scalar. Three thin adapters sit over the core — the
callables have **incompatible signatures and must not be conflated**:

| consumer | signature | returns |
|---|---|---|
| `BestOfN`/`Refine` (§6) | `reward_fn(args, pred) -> float` | score |
| `dspy.Evaluate` / scalar optimizers | `metric(example, pred, trace=None)` | score |
| **GEPA** | `metric(gold, pred, trace=None, pred_name=None, pred_trace=None)` | `dspy.Prediction(score=, feedback=<text>)` |

GEPA's **per-gate textual feedback** ("AST gate: banned import `os`", "0 items: feed URL 404",
"unstable across two runs") — not the scalar — is the whole reason it is chosen. The reward_fn also
reads `pred.trajectory` / `pred.final_reasoning`, lowering the score on a forced RLM extraction.
The noisy stability gate stays a hard accept/reject (§6) and is **excluded** from the scalar to
keep selection low-variance.

**Feedback content (the actual signal).** The GEPA/`gate_failures` text must be **un-summarized and
concrete** — not "AST gate failed" but "AST gate: `import os` banned at line 12; use
`urllib.parse`/`selectolax`"; not "0 items" but "0 items: `discovery_url` /feed returned 404, sitemap
had 0 article-like URLs — try the /news index". **Never** LLM-summarize `CandidateResult` before
handing it to `reflection_lm` (full traces scored 50.0 vs scores-only 34.6; *summaries were no better
than the scalar* [Meta-Harness]). Order by **severity within the pipeline** (not raw authoring order)
and **cap the foreground to the single most-blocking failure** (top ~1–2; later retries surface the
next as gates clear — many co-equal directives degrade compliance [TextGrad, JudgeFlow]). Attribute by
**counterfactual**, not temporal order — phrase the failure as the upstream `discovery_plan` decision
that, *if changed, clears the downstream gate* ("discovery_plan chose scrape; a feed existed at /feed —
feed would have yielded items"), the only thing GEPA can change [JudgeFlow] — and include the **delta**
("what would fix it") [GEPA, Feedback-Descent]. Mis-attributed feedback scores *below none*
[Feedback-Descent], so this is correctness-critical, not style. This is a pure transform over the already-computed
`CandidateResult` (~2% of eval cost — no extra LLM-judge pass). Additionally feed **across-run
aggregates**: condense the §4.9 run-history + §4.8 staleness per accepted site into per-site failure
summaries and thread them in — patterns invisible in any single trace surface only in aggregate,
which is where every large gain came from [HALO], and aggregation denoises single-run flakiness.

**Trainset.** `list[dspy.Example]`, one per accepted generation:
`dspy.Example(site_url=…, corpus=…, render=…, hints=…, discovery_method=…, discovery_url=…,
crawler_source=…).with_inputs("site_url", "corpus", "render", "hints")` (mirrors the four
`GenerateCrawler` inputs; `.with_inputs` is mandatory). It is **inputs-only / reference-free** —
the metric scores by *running a fresh crawler*, so the recorded `discovery_*`/`crawler_source` are
provenance, not labels. **Admission rule:** admit a `(site, corpus)` pair **iff** §6 **gates 1–3** pass, the §8 adversarial
cross-validation passes, and the site stayed non-stale (§4.8) for `AUTOCRAWLER_TRAINSET_STABLE_RUNS` (~3, decoupled from and more
conservative than the §4.8 single-run regen trigger) runs (a stale site yields a misleading reward and
pollutes GEPA's frontier; this is also why a `new_items`-based staleness reading would wrongly starve the
freshest sites). §6 **gate-4** (human approval) is **not** a
precondition — it gates *enabling a script on the crawl path*, a separate concern; the §4.11 loop is
offline and automatic. The frozen `corpus` dict is persisted with each example (plain JSON) so generation replays
reproducibly, under a bounded retention cap. The trainset lives in its own **logical namespace** (own
tables/dir — a different lifecycle from operational crawl state), though it **may co-locate** in the
state-store backend in v1; the disjoint train/val/promotion *pools* are row-level, not a separate DBMS.

**Saturation is the headline failure mode.** Because admission keeps only gate-passing, non-stale
crawlers, every example is near-success — and a near-binary reward then makes **GEPA no-op** (its
reflection LM never fires when every sampled minibatch scores all-correct; observed on three real
runs) [GEPA-+25pts]. The graded scalar is a **precondition, not the guard** — a *graded* 3-axis metric
still no-opped on a perfect valset at iteration 1 [GEPA-+25pts]. Mitigate: (i) graded scalar (§6) so
passing crawlers *can* differ; (ii — load-bearing) `optimize.py` asserts a **minimum fraction of
sub-maximal scores in the sampled minibatches at compile time** (not just the trainset mean), and on
`baseline ≈ optimized` emits the three-way remedy — re-bias/harden the trainset, run on a *cheaper*
optimizer executor to manufacture failure signal (promotion still re-scores on the deployment tier), or
accept saturation and defer; (iii — load-bearing) **bias** GEPA's train/val toward *hard /
discriminative* cases (recently-drifted, many-retry, barely-passing) — difficulty, not count, supplies
signal [Meta-Harness, HALO]. Also assert **per-instance spread**: GEPA's Pareto sampling consumes
per-site score *variance*, so a scalar high-variance across the trainset but flat per-site still
collapses sampling to uniform [GEPA, TRIPLE]. Admit a pair only after the §8 adversarial
cross-validation.

**Optimizer ladder.** Escalate by trainset size, not straight to GEPA:
- *No trainset* → run no optimizer; keep §6's `BestOfN`/`Refine` (needs none).
- *~5–20 pairs* → **generalization-guided few-shot exemplar selection** over the owned predictor
  (`BootstrapFewShotWithRandomSearch`): a pair's value = how much including it as a demo raises
  crawler-acceptance on *other* frozen snapshots (held-out transfer), with the scoring core as the
  utility. Highest-ROI, cheapest lever and available long before GEPA [CodeAdapt] — the rung the
  earlier draft skipped. Select by held-out transfer **with a diversity step** (cluster candidate
  exemplars by `site_features` and take the best-transfer one per cluster, not top-K by raw transfer);
  the chosen demo set must beat the **zero-demo** baseline by the §4.11 margin, since few-shot can
  *degrade* strong backbones; use the *graded* scalar as utility — a binary one collapses the ranking
  [TRIPLE, R1, BetterTogether].
- *feedback-rich regime* → **GEPA** (instruction evolution), justified by *feedback-richness*, not low data.
- *~200+ pairs* → **`MIPROv2` configured for JOINT instruction+demo** optimization (demos bootstrap
  from the inputs-only trainset; the gate-accept threshold *is* MIPRO's λ). Do **not** run
  instruction-only/0-shot MIPRO — the weaker lever in 6/7 tasks [MIPRO]. *v1 documents but does **not**
  build this rung — 200+ accepted pairs is multi-month-to-never for a near-static list; keep only its
  `MIPROv2(auto=)` signature smoke test.*

**Three disjoint pools — assert it.** GEPA internally splits its training data into `D_feedback`
(reflective minibatches) and `D_pareto` (its candidate-selection set, = our `valset`, passed
explicitly to `compile`). The **promotion held-out** is a *third*, frozen pool GEPA never sees, used
only by `dspy.Evaluate` after compile. The earlier draft conflated `valset` with the promotion set —
a leak. Assert (and M5-test) zero overlap across train / val / promotion; gskill's ~67/17/20
(200/50/60) split is a reasonable default [GEPA, RLM♥GEPA, gskill].

**GEPA config.** 5-arg feedback metric; `reflection_lm` = strong (`AUTOCRAWLER_REFLECTION_MODEL`,
distinct from `sub_lm`, ideally able to read raw traces); exactly one budget (`auto="light"` to start
— expect ~O(5) accepted mutations, gains early, tens of minutes/task at thread=1; a low accept-count
is *not* failure); `compile(..., teacher=None)`; `track_stats=True`. **Validation dominates the
budget** (train rollouts to optimum can be ~6–737), so keep `valset`/`D_pareto` **small** — each
rollout is a full sandboxed crawl — and cap it as a knob; keep `minibatch≈3`; **retain Pareto
sampling** (a greedy selector collapses to a local optimum after ~one iteration); leave **Merge OFF**
for v1 (single owned predictor; Merge is model-sensitive and can regress weaker models)
[GEPA, GEPA-+25pts].

**Cost lever: optimize cheap, deploy strong.** The offline run MAY use a *cheaper* generation
executor — the optimized **text** artifact transfers *up* to the strong pinned online executor
unchanged [RLM♥GEPA, gskill]. Make this a **gate, not an assumption**: run the held-out promotion eval
on the *deployment* executor tier (and `sub_lm`), so a "weak-model-crutch" artifact that only helps
the cheap optimizer is caught.

**Data sizing — reconciled.** Keep ~300 as the long-run *corpus* target for diversity/coverage, but
make each GEPA run's train/val a **small, hard, discriminative subset** (~50 full evals/run is the
norm; each is a sandboxed crawl) — a small discriminative valset beats a large frozen one
[Meta-Harness, MIPRO].

**Promotion gate (regression-safe).** Evaluate candidate **and incumbent** on the *same* frozen
fixtures, offline (never live — that would hammer real sites, §4.5, and is non-deterministic),
averaging ≥2–3 passes (or temperature=0/seeded) since each gate run spins a fresh non-deterministic
RLM sandbox. Promote only if the candidate beats the incumbent by a **margin exceeding eval noise**
(default ≥ one held-out fixture's weight, 1/|held-out|) — *not* strictly-greater on one pass, which
promotes noise (HALO reports a noisy "peak"; DGM measured ~2.3% cross-run std) [HALO, DGM,
GEPA-+25pts]. **Co-gate on an independent true-quality signal:** the candidate must *also* beat the
incumbent on the §8 adversarial cross-validation pass-rate over the promotion fixtures (titles match the
live `<title>`, URLs resolve, low boilerplate/duplicate/off-domain) plus HALO drift/staleness — promote
only if **both** the §6 proxy *and* this independent signal improve. If the proxy rises while adversarial
quality is flat/down, **reject as over-optimization** (proxy fit doesn't track true quality — it's
hump-shaped and starts early [`#acb2cc`, R1 `#494c25`]); log the proxy-vs-true divergence as a standing
tripwire, and record the **public-vs-private gap** (optimizer-facing `valset` score vs frozen promotion
score) as a memorization/overfit alarm [ShinkaEvolve `#f3ea65`]. Record the per-fixture scores +
accepted-mutation count in the versioned artifact for observability.
*(Post-v1, once the held-out is large enough to populate buckets — a sub-60 held-out can't fill a 3-D
`discovery_method × render × Cloudflare-class` stratification: add a hard per-stratum / backward-
transfer guard (no bucket or fixture regresses below incumbent by >ε), split drift vs cold-start
subsets with per-subset deltas [HALO, gskill, AWM], and capability-vs-harness triage that excludes
sites unaccepted across K regenerations from the trainset/split [HALO]. v1 ships only the margin gate
above.)*

**Trigger & persistence.** Out-of-band — manual (the optional §4.9 HTTP surface), a slow
`AUTOCRAWLER_REOPT_CHECK` cadence, and/or a trainset-growth threshold of *+M* health-confirmed
pairs. The optimized program persists as **JSON state** (`generator.save("generator.json")`,
`save_program=False`) — **never** cloudpickle (`save_program=True` is arbitrary-code-exec on load,
contrary to §8). `generator.py` reconstructs `CrawlerGenerator` and `.load()`s on boot, else falls
back to baseline. Artifacts are versioned/timestamped (tagged with trainset snapshot + held-out
score), the prior kept for rollback via a single config pointer, and the DSPy version pinned
alongside. The generator program is reviewed separately from per-crawler review (§8).

The optimized state is **declarative text** (the strategy instruction / a strategy "skill" string
injected into the prompt) — instruction-string and typed-predictor targets are interchangeable, and a
plain text artifact trivially round-trips through JSON, **de-risking** the §14 typed-predictor
question (the published precedents [RLM♥GEPA, gskill] all evolve a *text* artifact, never a typed
predictor end-to-end). GEPA must **not** be extended to emit/evolve crawler *code* (out of scope).
Acceptance criteria: a `save → load → re-score` round-trip yielding identical held-out scores; a
**tested rollback** (flip the config pointer to the prior `generator.json`, confirm the regeneration
loop recovers); a **predictor-is-load-bearing** check (perturb the strategy instruction to two
deliberately-divergent texts and assert measurable behavioral divergence in generation outcomes — a
no-op-but-round-trips predictor passes the identity test yet fails this, so pin *where* `discovery_plan`
enters the `GenerateCrawler` prompt and exercise that wiring); and a **pre-build toy-example milestone**
proving the chosen owned predictor GEPA-optimizes, JSON-saves, reloads, and re-scores identically
*before* committing to that mechanism — else default to the instruction-string / CodeAct path.

**Honest payoff.** This does not make "every site better over time" — the input is a near-static
`sites.yaml`. It optimizes for (a) **regeneration quality under drift** (§4.8) and (b) **cold-start
on newly-added/unseen sites**, validated on the held-out split.

## 5. Data & interface contracts (summary)

- **Site entry** — §4.1.
- **Generated module** — `SITE: str`, `crawl(ctx) -> Iterable[Item]`; allowlisted imports only (§4.3).
- **`ctx`** — `fetch`, `render`, `get`, `log`; enforces robots/rate-limit/timeouts/allowed-hosts.
- **`Item`** — §4.6; maps to `POST /news {title, body}`.
- **Run record** (canonical) — `{site, started, finished, items_found, new_items, errors, pages_fetched}` in the state store; §4.8/§4.9 reference this, not re-list fields.
- **`CandidateResult`** — frozen Pydantic model (`scoring.py`): `item_count:int`, `samples:list[dict]` (capped), `schema_errors:list[str]`, `pages_fetched:int`, `exception:{type,message,traceback_tail}|None`. The §6 scoring-core output; all three §4.11 adapters + the feedback transform + §8 admission read these exact fields.
- **Trainset example** — `dspy.Example(site_url, corpus, render, hints, discovery_*, crawler_source).with_inputs("site_url","corpus","render","hints")`; inputs-only (§4.11).

## 6. Generation acceptance gates
A generated script is accepted only if it passes, in order:
1. **Static analysis** — AST allowlist; no banned imports/builtins; matches the §4.3 contract.
   Consumes `crawler_source.code` (the field is a `dspy.Code`, fences already stripped — §4.2).
2. **Runtime validation & completeness** — yields ≥ `min_items`; every item has non-empty `title`,
   a **sufficiently long, non-boilerplate** `body` (and reject bodies above a small U+FFFD
   replacement-char ratio — mojibake from mis-detected encoding), and a valid on-domain `url`; runs within
   time/page/memory budget; obeys robots. Plus two anti-under-enumeration checks — a news crawler is
   an *exhaustive-enumeration* task and RLMs reliably under-enumerate on "find every X" (e.g. 9–26 of
   ~114) [Sanjaya]: (a) **must-observe** — reject/down-score a candidate that never called
   `validate_crawler` before `SUBMIT`, or chose `discovery_method="scrape"` without first consulting
   `find_feeds`/`get_sitemap` (the docstring's "validate first" is *enforced*, not merely requested);
   (b) **enumeration floor** *(deferred to M5/post-v1 — needs a calibrated `ENUM_FLOOR_RATIO` per site
   class)* — compare the crawler's URL count against the host-side feed/sitemap entry count in the
   corpus, flagging a homepage sampler that missed the feed. For v1, `min_items` + stability already
   reject trivially-few-item crawlers and must-observe (a) pushes feed-first. Scraping a homepage
   *samples* "recent"; a feed/sitemap *enumerates*.
3. **Stability** — two runs overlap by **Jaccard of canonical URLs ≥ a *low* global threshold**
   (§4.6 canonicalizes `url`): a hard reject only for the **near-random** case, with near-threshold
   overlap surfaced as *soft* feedback for the §8 review path (busy wires churn fast — a high global
   threshold would reject correct high-velocity crawlers; per-site-class calibration deferred, §14 Q8).
   *Excluded from the optimizer scalar* (§4.11), so a false reject can't poison selection/GEPA — it only
   narrows admission.
4. **Review gate** (§8) — for v1, default-on human approval before a script is enabled.

Gates 1–2 are computed by **one pure scoring core** that runs a candidate via `validate_crawler` and
returns a `CandidateResult`, a `gate_failures` reason string, and a **graded, continuous, multi-axis**
score — *not* a 0/1 gate verdict. Compute the hard pass/fail gates **first**, then combine the quality
axes over survivors by **shape-by-necessity**, *not* a flat 5-way product: a **min/AND** veto only for
the true veto axes where near-zero means worthless (on-domain-URL validity; arguably field-presence),
and a **bounded weighted sum** for the graded axes (items-yielded ratio, body-length rate,
discovery-method quality), minus a small forced-extraction tie-breaker. Every axis is **bounded to
[0,1]** before it enters the scalar — the items axis is `min(item_count/N, 1)`, *clamped*, never raw
count (exceeding N buys nothing and removes the unbounded-push incentive). This keeps the benefit the
spec wants — `item_count` can't buy back an empty-body or off-domain crawler — without a single
weak-but-acceptable graded axis cratering an otherwise-good one (a product has unbounded local
sensitivity as any axis → 0, so a near-zero *noisy* axis swings it hard). **Every working precedent
that collapsed quality axes used additive/weighted-sum — including this spec's own cited source
`#e643b2`, verified weighted-additive (0.55/0.30/0.15); pure-multiplicative count is 0** [Netflix
`#77d24b`, R1 `#494c25`, ReSeek `#fe1014`, retrieval-fusion `#dd2c1f`]. **Axis weights** are seeded
*non-uniformly* (heaviest on the items-yielded + body-length "real-articles" core, lightest on
discovery-method — the `#e643b2` prior), with the **fewest free weights** (~2–3 groups, not 5 knobs)
and **precision** axes (on-domain, body-non-boilerplate) above the **recall** axis [ReSeek];
calibrate by regressing axes against a downstream label (non-stale tenure) [Netflix].
**Discovery-method-quality is a distraction risk** — a low-signal axis can hurt more than mis-weighting
[Sotopia-RL `#bdb383`] (a feed crawler that under-enumerates is worse than a scrape crawler that
enumerates) — so run a **leave-one-axis-out** ablation before freezing it and **demote** it to feedback
text if it doesn't improve the composite's correlation with adversarial-cross-val pass-rate (feed-first
is already a hard gate-2 precondition). A binary or single-axis scalar **starves both** selection and
the optimizer (no partial-credit gradient); the combine form is **A/B'd vs the flat product on the
frozen held-out** (§14 Q8). Keep the noisy stability gate **out** of this scalar —
generalize the principle: route flaky signals as descriptive text, never into the optimization
target. *(M5b: add a low-weight efficiency tie-breaker — `pages_fetched`/turn-count — among gate-passing
candidates; on strong backbones cost/latency may be the only measurable optimization gain [gskill]. At
N≈4–8 it rarely changes the v1 argmax, so it's deferred.)* Generation runs the RLM through the §6
selection loop over the scalar; the **same
scoring core** (not the same callable) feeds the §4.11 optimizer through three signature-correct
adapters — `reward_fn(args, pred)`, `dspy.Evaluate`, and the 5-arg GEPA metric have incompatible
signatures and must not be conflated (§4.11).

**Candidate selection.** Generate N candidates and take the **argmax over the graded scalar**. They run
**sequentially** — stock `dspy.BestOfN`/`dspy.Refine` are sequential for-loops over rollouts with an
early break (verified in installed 3.2.1), *not* concurrent — so N costs ~N× wall-clock and ~N× spend
(sized against `FANOUT_SPEND_CAP`); the real win is the **early break** (the common case where the first
candidate passes), not parallelism. SRLM's +22% is a selection-**quality** result, not free concurrency.
For feedback-carrying retries use a **small custom loop**, not stock `dspy.Refine`: its hint comes from
`inspect.getsource(reward_fn)` + the scalar via an internal `OfferFeedback` predictor — it never sees our
`gate_failures` string (and `getsource` on a lambda/partial adapter throws). Add a `prior_feedback: str`
`InputField` to `GenerateCrawler` and re-invoke generation with the prior `CandidateResult.gate_failures`
injected (preserving the per-retry-feedback property [Feedback-Descent, Meta-Harness]); cap retry depth.
Plain best-of-N argmax (no extra feedback LM call) is the cheaper no-feedback fallback. Default N≈4–8.
*(Post-v1: true `asyncio.gather` parallelism and a Sequential-Halving allocator [TRIPLE] — deferred.)*

## 7. Project layout (mirrors `bizup-news` conventions)
```
autocrawler/
  pyproject.toml            # name="bizup-autocrawler"; [project.scripts] run/generate/crawl/validate
  flake.nix .envrc          # nix dev shell, like news (Deno + Playwright browsers provisioned here)
  sites.yaml                # the input site registry
  src/bizup_autocrawler/
    config.py               # all env knobs in one place (§11)
    sites.py                # load/validate sites.yaml
    generate/               # generation + self-improvement
      signatures.py         #   GenerateCrawler (+ owned strategy sub-signature, §4.11)
      tools.py              #   fetch/render/find_feeds/get_sitemap/validate_crawler
      snapshot.py           #   build the prefetched corpus dict: homepage/robots/sitemap/articles (§9)
      generator.py          #   CrawlerGenerator(dspy.Module): RLM + BestOfN/Refine; save/load (§4.11)
      scoring.py            #   one scoring core + reward_fn / Evaluate / GEPA adapters (§6, §4.11)
      optimize.py           #   offline GEPA job: trainset → compile → held-out promote (§4.11)
      evaluate.py           #   dspy.Evaluate over frozen fixtures (§4.11, §12)
    runtime/                # the crawl phase (no LLM)
      contract.py           #   Item model + crawl(ctx) protocol
      ctx.py                #   fetching + politeness + challenge escalation (§4.5)
      solver.py             #   ChallengeSolver backends + clearance cache (§4.10)
      sandbox.py            #   constrained execution of generated scripts (§4.4, §8)
      runner.py             #   import → crawl → normalize → dedup → ingest
    ingest.py               # POST /news client
    state.py                # seen-URL / health store (§4.7)
    trainset.py             # accepted (snapshot→crawler) examples for §4.11; separate store
    health.py               # staleness detection → regeneration (§4.8)
    schedule.py             # scheduler daemon (§4.9): due-selection, concurrency, retry, regen triggers
    cli.py                  # operator commands (§10)
    crawlers/               # GENERATED, committed: techcrunch.py, theverge.py, ...
    generator.json          # GEPA-optimized generator state (JSON, versioned; §4.11)
  tests/                    # mirror src; a hand-written MockInterpreter (dspy ships none), recorded HTTP + golden HTML fixtures
```

## 8. Security model (generated code that runs on a schedule)
The central risk is **executing model-written code against the live web**. Mitigations:
- **AST allowlist** on every generated module (imports, builtins, no dynamic exec).
- **Sandboxed execution** (`runtime/sandbox.py`): subprocess with CPU/memory/time limits,
  no filesystem writes, **network egress only via the `ctx` proxy**.
- **SSRF guard** in `ctx`: requests restricted to the site's registered host(s) and
  on-domain links; block private/loopback/link-local ranges and cloud metadata endpoints.
- **Review gate**: generated/regenerated scripts are written for human approval (e.g.
  opened as a PR) before being enabled. Configurable; default-on in v1 precisely because
  the artifact is generated code.
- **The harness is an immutable trusted computing base.** The acceptance gates, sandbox policy, AST
  allowlist, and SSRF guard are *never* authored, parameterized, fetched, or relaxed by
  model-generated code or by the GEPA-optimized predictor — only by version-pinned host code/humans.
  GEPA may rewrite the strategy instruction but can **never** widen the allowlist or weaken a gate
  (gates are fixed adjudicators, not optimization targets).
- **Hide the scorer from the optimized loop.** The scoring core's exact acceptance predicates and
  numeric thresholds are not exposed to the generator/RLM: feedback says *what* failed semantically
  ("0 items extracted"), never the predicate/threshold a candidate could target. Objective-hacking
  rose specifically when the scorer was *visible* to a self-modifying agent that reward-hacked its
  evaluator by deleting detection markers [Darwin-Gödel-Machine]; under-specified self-evolution drove
  reward-hacking / alignment-faking up to 78% [Self-Evolving-survey].
- **Adversarial admission.** The reward (item-count + field-presence + stability) is a gameable proxy
  — a crawler could pad/fabricate items or scrape off-domain. Before a pair is admitted as a positive
  exemplar (§4.11), cross-validate its items against independent signals (URLs resolve, titles match
  the live `<title>`, duplicate/boilerplate-body penalty, off-domain-URL penalty). Acceptance rests on
  the **actual** `CandidateResult`, never the model's *claimed* counts (the "imagined variable" risk).
- **Untrusted-XML hardening (hot path).** Feed/sitemap parsing (`feedparser`, `lxml`, `get_sitemap`)
  consumes attacker-influenced XML on *every committed crawl*. Disable external-entity resolution and
  entity expansion (`defusedxml`, or `lxml`/`feedparser` with `resolve_entities=False` / `huge_tree`
  off) to block XXE / billion-laughs. The parser libs (`lxml`/`selectolax`/`feedparser`/`httpx`) are
  part of the **pinned-image TCB** — a parser CVE is fixed by re-pinning the image, not regenerating a
  crawler. (The §4.4 ~10 s timeout + memory cap already backstop regex backtracking / RAM exhaustion.)
- **No LLM scores its own output; protect what the scorer reads.** No axis of the §6 scalar may be
  produced by an LLM judging generated output — the deterministic host core sets the score, and
  `reflection_lm` only edits the *instruction* from host-computed `CandidateResult` facts, never the
  generator's self-narrated transcript (on-policy self-assessment is biased, worst exactly on
  low-quality cases [Self-Attribution-Bias]); prefer a `reflection_lm` of a *different model family*
  from the generator. Hidden thresholds are necessary but not sufficient — DGM's hack deleted the
  markers the scorer *reads* — so every signal the scorer consumes is host code the optimized loop
  can't touch, and feedback names *what* failed but never a field/marker a candidate could blank out
  [Darwin-Gödel-Machine].

## 9. DSPy / runtime notes
- `dspy.RLM` is `@experimental` — pin DSPy `3.2.x` (already a dep of `bizup-news`).
- The **default RLM sandbox requires Deno** (Pyodide/WASM); provision it in `flake.nix`.
- RLM's Pyodide sandbox has **no network** — the model reasons over the prefetched corpus and
  reaches the live site only through the host **tools** in §4.2, never from inside the sandbox.
- **Two distinct sandboxes:** RLM's Pyodide REPL (where the model *reasons and drafts*) and the
  host execution sandbox (§4.4, where candidate and committed crawlers actually *run*). They
  never share a process; `validate_crawler` (§4.2) is the bridge.
- **`corpus` injection (the real dspy 3.2.1 mechanism — verify before M1).** `corpus` is a plain
  **JSON-able dict** passed as an RLM input. dspy's `PythonInterpreter._serialize_value` injects it as
  a Python literal and **raises `CodeInterpreterError` on any non-JSON type** (only
  None/str/bool/int/float/list/tuple/dict; tuples/sets → lists); values over `LARGE_VAR_THRESHOLD`
  (100 MB) spill to `/tmp/dspy_vars/{k}.json` and are `json.loads`-ed in-sandbox. There is **no**
  `SandboxSerializable`, no `to_sandbox`/`rlm_preview`, and **no pickle** in the installed 3.2.1 RLM
  path — the dspy-rlm *skill* describes an API that does not exist here (confirmed by grepping the
  pinned `bizup-news/.venv`). So shape `corpus` as an **addressable dict**
  (`{homepage, robots, sitemap, articles:[{url,title,text}]}`) of JSON primitives — addressable parts
  beat one blob (~6 pts / up to 8× fewer tokens [Coding-Agents, LLM-in-Sandbox]) — and define in-REPL
  `grep`/slice helpers as plain functions the prompt provides (Pyodide lacks grep/sed). Capture
  `crawler_source` via the REPL **FINAL channel** (bypasses the root LM's output-token limit); gate-1
  treats a non-parsing `crawler_source` as truncation.
- **Interpreter left at default.** Keep RLM's `interpreter=` at the default Pyodide/Deno; a custom
  host-CPython interpreter was considered and rejected — it would run the model's *ungated*
  exploratory code in a network-capable process, dissolving the no-network isolation §8 relies on.
  The host execution sandbox (§4.4) is wired only as the *gated* `validate_crawler` tool.
- **No importable `MockInterpreter`.** DSPy ships none on the wheel; author one satisfying the
  `CodeInterpreter` protocol (copy/adapt `bizup-news`'s `tests/reports/_mock_interpreter.py`), and
  drive the planner with `DummyLM` (`from dspy.utils.dummies`) for Deno-free, model-free tests.
- Obtain the LM via `dspy.context`, not the thread-owned `dspy.configure`; build it `cache=False`
  for generation (§4.2). Use `aforward`/async to match the news project's style.
- **Order gates cheapest-first**, short-circuiting on first failure: AST → single-run yield → two-run
  Jaccard stability → (offline only) multi-pass promotion eval, so the expensive multi-run evaluation
  runs only on candidates that cleared the cheap screens [Darwin-Gödel-Machine].
- **Pin every DSPy seam in a build-time smoke test — before M1.** Multiple spec claims already proved
  wrong against installed 3.2.1 (the nonexistent `SandboxSerializable`; an "unbounded root loop"; stock
  `Refine` carrying feedback), so one early CI smoke test asserts against the *installed* dspy: the
  **corpus-injection seam** — `PythonInterpreter._inject_variables(code, {"corpus": d})` accepts a
  well-formed dict and **raises `CodeInterpreterError` on a poisoned one** (non-JSON value / set of
  dicts) — *not* `serialize_for_json`, which falls back to `str(value)` and never raises (so the obvious
  test can't fire); the **async entrypoint** — `CrawlerGenerator.aforward` resolves and does *not*
  delegate into a missing `BestOfN`/`Refine` `aforward` (they implement only sync `forward`); the
  GEPA/MIPROv2 signatures (5-arg feedback metric, `reflection_lm`, `auto=`, `teacher=None`,
  `MIPROv2(auto=)`); `named_predictors()` non-empty before any `compile`; JSON `save`/`load` round-trip;
  and FINAL-channel `crawler_source` capture. Each speculative claim becomes a fast failing test.

## 10. CLI & operations
| command | does |
|---|---|
| `autocrawl run` | **start the scheduler daemon** — crawl due sites forever, retry, trigger regeneration |
| `autocrawl generate <slug> \| --all \| --stale` | run RLM, gate (§6), write `crawlers/<slug>.py` |
| `autocrawl validate <slug>` | dry-run a script, report items/errors, **no ingest** |
| `autocrawl crawl <slug> \| --all` | run committed scripts, dedup, ingest into news |
| `autocrawl list` | show sites, script presence, health/staleness |

`autocrawl run` is the primary deployment entrypoint (the service schedules itself); `crawl`
stays for manual one-shot runs and CI.

## 11. Configuration (env, all in `config.py`)
| var | default | meaning |
|---|---|---|
| `AUTOCRAWLER_MODEL` | strong code-capable model | RLM root planner/extractor |
| `AUTOCRAWLER_SUB_MODEL` | concrete cheap LM (required) | RLM `sub_lm`; unset ⇒ every `llm_query` billed at root rates |
| `AUTOCRAWLER_REFLECTION_MODEL` | strong LM | GEPA `reflection_lm` for self-improvement (§4.11) |
| `AUTOCRAWLER_MAX_ITERATIONS` | e.g. `20` | RLM `max_iterations` (pass-through; installed default 20) |
| `AUTOCRAWLER_MAX_LLM_CALLS` | e.g. `40` | RLM `max_llm_calls` (soft/recoverable; pass-through) |
| `AUTOCRAWLER_MAX_SUBCALLS` | e.g. `60` (≥ MAX_LLM_CALLS) | **unconditional** host sub-call kill — the only hard stop on a catch-and-continue loop (§4.2) |
| `AUTOCRAWLER_FANOUT_SPEND_CAP` | e.g. `$8/site` | aggregate \$ ceiling across the selection fan-out (per-candidate `SPEND_CAP` deferred post-v1) |
| `NEWS_API_URL` | `http://localhost:8000` | `bizup-news` base URL for `POST /news` |
| `AUTOCRAWLER_STATE_URL` | local Postgres/SQLite | crawl-state + health store |
| `AUTOCRAWLER_USER_AGENT` | identifying UA string | sent on every request |
| `AUTOCRAWLER_RATE_LIMIT` | e.g. `1/s/host` | politeness cap |
| `AUTOCRAWLER_MAX_PAGES` | e.g. `200` | per-crawl page budget |
| `AUTOCRAWLER_FIRST_CRAWL_CAP` | e.g. `30` | newest-K items ingested on a site's *first* crawl (caps server-side LLM backlog, §4.7) |
| `AUTOCRAWLER_INGEST_RATE` | e.g. `2/s` | ingest throughput governor, sized to news-API LLM capacity (≠ §4.5 fetch cap) |
| `AUTOCRAWLER_MIN_ITEMS` | e.g. `5` | gate-2 min items (M1 placeholder; calibrate per §14 Q8) |
| `AUTOCRAWLER_MIN_BODY_CHARS` | e.g. `400` | gate-2 min stripped body length (M1 placeholder) |
| `AUTOCRAWLER_STABILITY_JACCARD` | e.g. `0.6` | gate-3 URL-overlap threshold (M1 placeholder) |
| `AUTOCRAWLER_ENUM_FLOOR_RATIO` | e.g. `0.5` | gate-2 enumeration floor vs feed/sitemap count (M1 placeholder) |
| `AUTOCRAWLER_TRACKING_PARAM_DENYLIST` | `utm_*,fbclid,gclid,…` | query params stripped in URL canonicalization (§4.6) |
| `AUTOCRAWLER_DEFAULT_SCHEDULE` | e.g. `30m` | crawl cadence for sites that set none |
| `AUTOCRAWLER_CONCURRENCY` | e.g. `4` | max sites crawled concurrently |
| `AUTOCRAWLER_REGEN_CHECK` | e.g. `24h` | how often the scheduler evaluates staleness |
| `AUTOCRAWLER_REOPT_CHECK` | e.g. `off` / `7d` | cadence of the offline self-improvement job (§4.11) |
| `AUTOCRAWLER_TRAINSET_STABLE_RUNS` | e.g. `3` | sustained healthy runs before a pair is admitted to the trainset (§4.11) |
| `AUTOCRAWLER_SOLVER` | `stealth \| flaresolverr \| none` | challenge-solver backend (§4.10) |
| `FLARESOLVERR_URL` | — | FlareSolverr endpoint when that backend is selected |
| `AUTOCRAWLER_CLEARANCE_TTL` | e.g. `30m` | how long a cached `cf_clearance` cookie is reused |
| `AUTOCRAWLER_EGRESS_PROXY` | — | shared egress proxy for solver + cheap requests (IP/UA pinning) |
| `AUTOCRAWLER_REVIEW_GATE` | `on` | require approval before enabling a generated script |

## 12. Testing strategy
- **Generator** — `dspy.RLM` driven by an autocrawler-authored `MockInterpreter` (dspy ships none
  importable; queued REPL responses) + `DummyLM`; assert tool wiring, that the scoring core + the
  reward_fn adapter score correctly (the Evaluate/GEPA adapters are M5b), that a forced extraction
  (`final_reasoning`) is caught, and that bad output is rejected. No Deno, no live network.
- **Self-improvement (§4.11)** — `dspy.Evaluate` over frozen corpus snapshots + recorded fixtures;
  assert: the promotion gate refuses a non-improving candidate **and** one that wins on aggregate
  while regressing a stratum (margin + per-bucket guard); `save`/`load` JSON round-trips the program
  to identical held-out scores; train/val/promotion pools are **disjoint**; `named_predictors()` is
  non-empty before `compile`; a tested **rollback** restores the prior generator; the **saturation**
  assertion fires when the trainset is all-near-maximal; that `_inject_variables` injects a well-formed
  `corpus` and **raises `CodeInterpreterError`** on a poisoned one (the real REPL guard —
  `serialize_for_json` never raises); that `CrawlerGenerator.aforward` resolves without delegating into a
  missing wrapper `aforward`; and a build-time smoke test pins the GEPA/MIPROv2 call signatures against
  installed DSPy.
- **Runner/contract** — golden HTML/feed fixtures + recorded HTTP; assert normalized items
  and dedup behavior. A couple of hand-written reference crawlers double as contract tests.
- **Politeness/SSRF** — unit tests proving robots/rate-limit/allowed-host enforcement.
- Mirror `tests/` to `src/`, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), `ty`, `ruff`.

## 13. Milestones
- **M0 — Path proof:** contracts + runner + one *hand-written* crawler + `POST /news`. No LLM.
- **M1 — Generator (gated):** `dspy.RLM` + host tools + `validate_crawler` loop, wrapped in `CrawlerGenerator(dspy.Module)` (named-predictors + JSON round-trip); generate over ~15–20 real seed sites; acceptance gates (§6); selection via the sequential best-of-N / custom-feedback loop.
  - **Step-0 (days, zero new code):** run `find_feeds`+`get_sitemap` over the seed list, tabulate feed/sitemap availability + per-site Cloudflare class. This table decides architecture weight: feed-abundant ⇒ feed-first *is* v1 and the §4.10 stealth tiers + HTML-scrape generation **defer**; feed-poor ⇒ the solver tier moves earlier and is de-risked first.
  - **Go/no-go exit:** measure gate-1+2 pass-rate and median \$/site; a low pass-rate on feed-bearing sites (e.g. <70%) is a **no-go** — **M2 is contingent on this number** (the Sanjaya under-enumeration risk makes the keystone "RLM reliably writes working crawlers" something to *measure*, not assume).
  - Commit to RLM; CodeAct stays a documented fallback; the backbone A/B rides for free on the M5b held-out split — *not* an M1 deliverable.
- **M2 — Scheduler & robustness:** the `autocrawl run` scheduler (due-selection, concurrency, retry/backoff) + run history; dedup/state, politeness, headless tier, Cloudflare challenge tier (§4.10), SSRF/sandbox hardening.
- **M3 — Self-healing:** health tracking + single-run (`items_found`) stale detection + scheduler-driven regeneration with review gate; drift-MTTR tracked as an observability metric (§4.8).
- **M4 — Operate:** metrics/observability, optional status/control HTTP surface.
- **M5a — Self-improvement floor (core):** the cheap, always-valid part — `CrawlerGenerator(dspy.Module)`
  with `named_predictors()` non-empty + JSON `save`/`load` round-trip; the inputs-only trainset
  accumulating under the §4.11 admission rule (`AUTOCRAWLER_TRAINSET_STABLE_RUNS` sustained healthy runs);
  the **hidden-scorer + immutable-TCB invariant** (a standing §8 invariant — binds the moment any
  optimizer touches feedback); the cheapest optimizer rung only (`BootstrapFewShotWithRandomSearch` over
  the owned predictor); and the pre-build toy validation (GEPA-optimize → JSON round-trip → re-score
  identical, **plus** the predictor-is-load-bearing perturbation check, §4.11). The graded multi-axis
  reward is **not** here — it's §6/M1 infra the selection path already needs.
- **M5b — GEPA evolution (deferred behind a difficulty bar):** GEPA instruction-evolution + the 5-arg
  feedback adapter + the `dspy.Evaluate` adapter + the three-pool overlap asserts + the saturation-floor
  assertion + the regression-safe margin promotion gate. These bind only once the trainset crosses a
  **difficulty bar** (~15 examples with sub-maximal graded scores + a few drift/cold-start cases) — until
  then M5a's cheap rung is the standing deliverable. `MIPROv2` stays documented-but-unbuilt (§4.11). The
  free backbone A/B (RLM vs CodeAct) rides on M5b's held-out split. Payoff: better regeneration-under-drift
  + cold-start on unseen sites, *not* a monotonically improving fleet. Both GEPA and RLM are
  `@experimental`; `dspy.CodeAct` is the fallback if the owned predictor optimizes unevenly.

## 14. Open questions / follow-ups
1. **News API idempotency** — recommend extending `POST /news` to accept `url`/`source`/`published_at`
   and an idempotency key, so dedup is also enforced server-side (today it's crawler-side only).
2. **Regeneration delivery** — auto-commit vs. open a PR for the new script. (Spec assumes PR/review.)
3. **State store** — Postgres (matches stack) vs. SQLite (simpler) for v1.
4. **Scheduler scale-up** — v1 runs an in-process async scheduler (§4.9); revisit a task queue (arq/Celery) only if site count or crawl volume outgrows one process.
5. **Site list ownership** — who edits `sites.yaml`, and is per-site `hints` enough steering?
6. **Challenge backend** — start with FlareSolverr (fastest to wire) or an in-process stealth
   browser (fewer moving parts)? Decide empirically against the real target sites, and settle the
   egress/proxy strategy (IP+UA pinning) and whether a paid solver is ever allowed for Turnstile.
7. **Typed-predictor round-trip** — *de-risked*: the optimized artifact is declarative **text**, which
   round-trips trivially, and the precedents ([RLM♥GEPA], [gskill]) evolve a text artifact. Residual
   risk is whether a *typed* `dspy.ChainOfThought` predictor round-trips end-to-end; the pre-build toy
   milestone (§4.11/§13) settles it, and the instruction-string/CodeAct path is the default if not.
   Pin the GEPA/MIPROv2 call signatures via a build-time smoke test (§9).
8. **Metric calibration (a protocol, not guesses)** — the scalar's *combine form* (shape-by-necessity
   vs flat product), the axis weights, and the numeric thresholds (`min_items`, body chars, Jaccard,
   `ENUM_FLOOR_RATIO`) are *calibrated*, not asserted: (a) **A/B the combine form** on the frozen
   held-out by argmax-matches-human-preference + per-instance spread [retrieval-fusion]; (b) **sweep
   each threshold** to maximize score spread between known-good and hand-broken
   (off-domain/boilerplate-padded) fixtures, take the robust *plateau* (distrust knife-edge optima),
   then ~50-example human-agreement rounds [ReSeek, Netflix]; (c) **seed weights** from the `#e643b2`
   prior with the fewest free weights and regress against a downstream label (non-stale tenure)
   [Netflix]. All thresholds stay host-side in the immutable TCB. Saturation/overfitting remain *known*
   failure modes (§4.11), not open questions.

## 15. Evidence base

Decisions above are grounded in the local research corpus (cited inline by short tag). Key sources and
what they anchor — full per-paper findings live in workflow `wqnhxlk46`:

- **GEPA: Reflective Prompt Evolution** (`#50c21d`) — `D_feedback`/`D_pareto` split, Pareto sampling, validation dominates budget, `minibatch≈3`, Merge model-sensitivity → §4.11.
- **"how GEPA lifted a 1.2B model 25 pts"** (`#e643b2`) — the **saturation no-op**, graded reward + hard-case bias, `auto="light"` profile, per-example "trap" feedback → §4.11, §6.
- **RLM ♥ GEPA** (`#286485`) — GEPA evolves an external *text* skill on an RLM (never internals); optimize-cheap/deploy-strong transfer; three-way held-out → §4.11.
- **HALO / Recursive Agent Optimization on Terminal Bench** (`#472657`, `#aa636b`) — aggregate trace decomposition is the gain source; per-stratum regressions + noisy "peak"; capability-vs-harness triage → §4.11.
- **Meta-Harness** (`#c78a92`) — full traces ≫ scores-only, *summaries no better than the scalar*; small-hard-discriminative set → §4.11 feedback + data sizing.
- **Darwin Gödel Machine** (`#4008a8`) — self-modifying agents reward-hack a *visible* evaluator → immutable TCB + hidden scorer + promotion margin > noise → §8, §4.11.
- **SRLM** (`#92bd81`) & **TRIPLE** (`#d550b5`) — K-sample selection *quality* (run sequentially) beats single-trajectory (+22%); Sequential-Halving over argmax/UCB deferred post-v1 → §6 selection.
- **Coding Agents are Long-Context Processors** (`#9b7ef3`) + **RLM** (`#ea21e9`) + **From Scratch** (`#0bbf98`) — code+environment substrate; addressable parts ≫ blob; recursion not the driver / hurts strong models; root loop bounded by max_iterations (soft max_llm_calls); JSON-literal variable injection (no pickle / no SandboxSerializable); FINAL channel → §4.2, §9.
- **MIPRO** (`#21498b`) + **CodeAdapt** (`#c4ad60`) — joint instruction+demo ≫ instruction-only; vacuous seeds underperform; exemplar selection is the cheap early lever → §4.11 ladder + seed.
- **Agent Workflow Memory** (`#971a5a`) + **gskill** (`#39b2ab`) + **BetterTogether** (`#5417b9`) — gains widen with the train/test gap; cross-model skill transfer + efficiency-only gains on strong models; prompts-before-weights (weights out of scope v1) → §4.11.
- **Survey of Self-Evolving Agents** (`#b7bd66`) — offline/online taxonomy, BWT/FGT, golden-dataset + tested rollback, quantified reward-hacking/alignment-faking risk → §4.11, §8.
- Supporting: **LLM-in-Sandbox** (`#00c82a`), **CodeAct** (`#7a8470`), **Sanjaya** (`#745267`), **JudgeFlow** (`#e3bbdd`), **Feedback Descent** (`#244e30`), **TextGrad** (`#be34e3`).
- **Metric design** (§6 scalar / §4.11 / §8) — additive/weighted-sum over multiplicative for collapsing quality axes, bounded sub-scores, gate-then-combine, calibration, over-optimization & off-policy reward: **Netflix** (`#77d24b`), **retrieval-fusion** (`#dd2c1f`), **ReSeek** (`#fe1014`), **R1** (`#494c25`), **Sotopia-RL** (`#bdb383`), **iStar/over-optimization** (`#acb2cc`), **ShinkaEvolve** (`#f3ea65`), **Self-Attribution-Bias** (`#5622de`). Full findings: workflow `w0iga2kgj`.
