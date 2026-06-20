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
offline optimization loop (§4.11), kept strictly off the crawl path.

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
    corpus: SiteSnapshot   = dspy.InputField(desc="prefetched homepage, robots.txt, sitemap, sample articles")
    render: str            = dspy.InputField(desc="static | browser | solver | auto")
    hints: str             = dspy.InputField(desc="optional operator guidance")
    discovery_method: Literal["feed", "sitemap", "scrape"] = dspy.OutputField(desc="how articles are discovered")
    discovery_url: str     = dspy.OutputField(desc="the feed / sitemap / index URL used")
    crawler_source: dspy.Code = dspy.OutputField(desc="Python module implementing the contract")

# crawler_source is a dspy.Code (markdown fences auto-stripped); gate-1 and the write step
# consume crawler_source.code. SiteSnapshot is a dspy.SandboxSerializable (§9): the large,
# multi-part HTML rides into the REPL as a real object — the model sees only a structured
# preview. Never pass megabytes as a plain str field.
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
| `validate_crawler(source)` | fence-strip, then run the candidate in the execution sandbox (§4.4) against the live site; return a **size-bounded** `CandidateResult` (capped `samples`, condensed `schema_errors`, exception = type+message+traceback *tail*) so it survives the REPL's 10k head+tail truncation. Crosses the bridge as JSON the model parses in-REPL. |

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
`AUTOCRAWLER_MAX_LLM_CALLS`): `max_llm_calls` caps the *count* of sub-LLM calls (a recoverable
in-sandbox raise), **not** runtime or spend — pair a bounded `max_iterations` with an outer
wall-clock/spend cap, especially under `BestOfN`/`Refine`/GEPA multiplication. The default
sandbox needs **Deno** installed.

**Output handling:** `crawler_source` is written to `crawlers/<slug>.py` (and, by
default, surfaced for human review — see §8) only after it passes the §6 acceptance gates.

### 4.3 Crawler script contract (the generated artifact)
Every generated module exposes a fixed interface so one runner can execute any of them:

```python
SITE = "https://example.com"          # module-level constant

def crawl(ctx) -> Iterable[Item]:     # yields normalized items; no network except via ctx
    ...
```

- `ctx` provides `ctx.fetch(url)`, `ctx.render(url)`, `ctx.get(url, **kw)`, `ctx.log(...)`,
  and parsing helpers. **All fetching goes through `ctx`** so limits are enforced.
- The module may `import` only from an allowlist (parsers like `selectolax`/`lxml`,
  `feedparser`, stdlib `re`/`json`/`datetime`/`urllib.parse`). No `os`, `subprocess`,
  `open`, `socket`, `eval`/`exec` (enforced by AST check, §8).

### 4.4 Runner & execution sandbox
- Imports `crawlers/<slug>.py`, calls `crawl(ctx)`, and consumes the yielded items.
- Runs the module under resource limits: wall-clock timeout, max pages, memory cap,
  no filesystem writes, network egress **only** through the `ctx` proxy.
- A failing script (exception, zero items, schema violation) is isolated to its site and
  recorded as a health signal — it never aborts the whole crawl.

### 4.5 Politeness & fetching (`ctx`, shared by all crawls)
Centralized so generated scripts can't get it wrong: `robots.txt` compliance, per-host
rate limiting + concurrency caps, retries with backoff, conditional GET (ETag /
Last-Modified), request timeouts, and an identifying `User-Agent`. `ctx.fetch`/`render`
restrict requests to the site's host(s) and on-domain article links (SSRF guard, §8). On a
detected Cloudflare challenge, `ctx.fetch` transparently escalates to the browser/solver tier
and caches the clearance cookie (§4.10) — scripts stay oblivious.

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

### 4.7 Dedup & crawl state
The autocrawler owns its own small state store (decoupled from the news DB): a set of
seen canonical URLs (and/or content hashes) per site, plus per-run health counters.
Postgres fits the existing stack; SQLite is acceptable for v1. Only **unseen** items are
ingested.

### 4.8 Health & self-healing regeneration
Each crawl records `items_found`, `new_items`, `errors`, `pages_fetched` per site. A site
is flagged **stale** when it yields zero items for *K* consecutive runs or its error rate
crosses a threshold. Stale sites are re-entered into §4.2 generation; the new script is
validated and proposed to replace the old one (review gate per §8). This is the
"regenerate when the site changes" loop made automatic.

Regeneration runs the **current** generator. If generation exhausts its budget without a
gate-passing crawler — including a forced RLM extraction (`final_reasoning == "Extract forced
final output"`, §4.2) — the site enters a distinct **generation-failed** state, surfaced
separately in `autocrawl list` and *not* silently re-enqueued into the same failing generation
(which would spin). Improving the generator itself is the separate offline loop in §4.11.

### 4.9 Scheduler / orchestrator (owns the run loop)
`bizup-autocrawler` runs as a **long-running service** — `autocrawl run` starts an async
scheduler that operates the whole crawl loop itself, rather than relying on an external cron.

- **Due-selection.** Each site has a cadence (`schedule` in §4.1, else `AUTOCRAWLER_DEFAULT_SCHEDULE`).
  The scheduler keeps `next_run` per site in the state store and dispatches sites whose time has come.
- **Bounded concurrency.** A global cap (`AUTOCRAWLER_CONCURRENCY`) limits how many sites crawl at
  once; per-host limits come from the politeness layer (§4.5). A site never overlaps its own prior run.
- **Retry & backoff.** A failed crawl retries with exponential backoff + jitter; persistent failure
  feeds the health signal (§4.8) instead of stalling the queue.
- **Regeneration trigger.** On a timer (`AUTOCRAWLER_REGEN_CHECK`) the scheduler evaluates staleness and
  enqueues stale sites for RLM regeneration (§4.8) — one orchestrator drives both crawling and generation.
- **Re-optimization hand-off (§4.11).** A slow timer (`AUTOCRAWLER_REOPT_CHECK`) or a trainset-growth
  threshold enqueues the **offline** self-improvement job — but the scheduler never runs `.compile()`
  in-band (a multi-hour GEPA run, hundreds of metric calls each spinning a fresh RLM sandbox, would
  violate the never-stall guarantee). It hands off to an out-of-band job and later adopts the new
  versioned generator program.
- **Run history.** Each run records `{site, started, finished, items_found, new_items, errors}` to the
  state store, for observability and the staleness math.
- **Lifecycle.** Graceful shutdown drains in-flight crawls; state lives in the store, so the loop is
  crash-safe and restart-idempotent.

v1 uses an **in-process async scheduler** (no external broker) — appropriate for a checked-in list of
news sites; a task queue is a later scale-up (§14). An optional thin HTTP surface (status, manual
trigger) can mirror `bizup-news`'s FastAPI style if operators want one.

### 4.10 Cloudflare / anti-bot challenge handling
Many news sites sit behind Cloudflare, but most use it only as a CDN (plain `httpx` works), and
**feeds-first discovery** (§4.1) often sidesteps challenges entirely since RSS/sitemap endpoints
are frequently unprotected. When a real challenge appears, `ctx` handles it **transparently** —
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

**The generator is a `dspy.Module`.** Wrap the `dspy.RLM` (and its §6 `BestOfN`/`Refine` wrapper)
in `CrawlerGenerator(dspy.Module)` with `aforward(self, *, site_url, corpus, render, hints) ->
Prediction`. Sub-modules held as attributes are auto-registered as parameters — which is what
makes them visible to optimizers and to `save`/`load`. RLM stays primary; we do **not** invert to
CodeAct.

**What GEPA optimizes.** RLM exposes no documented optimizer-targetable predictor, so GEPA is
scoped to a **predictor we own** — an extracted `dspy.ChainOfThought` *strategy* sub-module
(`site_features -> discovery_plan`) the RLM/CodeAct loop executes, or the CodeAct fallback's
planner instruction — not RLM's internals. GEPA rewrites that predictor's docstring-instruction.
`dspy.CodeAct` is the fallback if the owned predictor optimizes unevenly. (Open mechanism §14:
confirm the chosen module round-trips through JSON `save`/`load`.)

**One scoring core, three adapters.** §6's gate logic is a single pure scoring core that runs a
candidate via `validate_crawler` and returns a `CandidateResult` + a derived float score + a
`gate_failures` reason string. Three thin adapters sit over it — the callables have
**incompatible signatures and must not be conflated**:

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

**Trainset.** `list[dspy.Example]`, one per accepted generation:
`dspy.Example(site_url=…, corpus=…, render=…, hints=…, discovery_method=…, discovery_url=…,
crawler_source=…).with_inputs("site_url", "corpus", "render", "hints")` (mirrors the four
`GenerateCrawler` inputs; `.with_inputs` is mandatory). It is **inputs-only / reference-free** —
the metric scores by *running a fresh crawler*, so the recorded `discovery_*`/`crawler_source` are
provenance, not labels. **Admission rule:** admit a pair only once its generation passed all four
§6 gates *and* stayed non-stale (§4.8) for *N* runs (a stale site yields a misleading reward and
pollutes GEPA's frontier). The frozen `SiteSnapshot` is persisted with each example (it is a
`SandboxSerializable`, not a str) so generation replays reproducibly, under a bounded retention
cap. The trainset lives in its own `trainset/` store, separate from §4.7 and §4.9.

**Optimizer & split.** GEPA, justified by *feedback-richness* (not low data). Mandatory config or
it hard-fails: the 5-arg feedback metric above; `reflection_lm` = a strong model
(`AUTOCRAWLER_REFLECTION_MODEL`, distinct from the cheap `sub_lm`); exactly one budget (start
`auto="light"`); `compile(..., teacher=None)`; `track_stats=True` to inspect the Pareto frontier.
GEPA **maximizes the trainset** and holds out a small `valset` (the prompt-optimizer 20/80 rule
does *not* apply); pass `valset` explicitly. Escalation: below a usable trainset, run no optimizer
— keep §6's `BestOfN`/`Refine` (no trainset needed); once ~200+ pairs accumulate and a 40+-trial
budget is acceptable, `MIPROv2(auto=…)` is an alternative/compose step. (Calibration: little signal
below ~30 pairs; aim 300+.)

**Promotion gate (regression-safe).** Evaluate the candidate with `dspy.Evaluate` on a **frozen
held-out split** of sites, run **offline against §12 recorded fixtures + frozen snapshots** — never
live (live eval would hammer real sites, §4.5, and is non-deterministic). Promote and `save()`
**only if it beats the incumbent** on that split. The held-out split is never optimized against;
GEPA gets train+val.

**Trigger & persistence.** Out-of-band — manual (the optional §4.9 HTTP surface), a slow
`AUTOCRAWLER_REOPT_CHECK` cadence, and/or a trainset-growth threshold of *+M* health-confirmed
pairs. The optimized program persists as **JSON state** (`generator.save("generator.json")`,
`save_program=False`) — **never** cloudpickle (`save_program=True` is arbitrary-code-exec on load,
contrary to §8). `generator.py` reconstructs `CrawlerGenerator` and `.load()`s on boot, else falls
back to baseline. Artifacts are versioned/timestamped (tagged with trainset snapshot + held-out
score), the prior kept for rollback via a single config pointer, and the DSPy version pinned
alongside. The generator program is reviewed separately from per-crawler review (§8).

**Honest payoff.** This does not make "every site better over time" — the input is a near-static
`sites.yaml`. It optimizes for (a) **regeneration quality under drift** (§4.8) and (b) **cold-start
on newly-added/unseen sites**, validated on the held-out split.

## 5. Data & interface contracts (summary)

- **Site entry** — §4.1.
- **Generated module** — `SITE: str`, `crawl(ctx) -> Iterable[Item]`; allowlisted imports only (§4.3).
- **`ctx`** — `fetch`, `render`, `get`, `log`; enforces robots/rate-limit/timeouts/allowed-hosts.
- **`Item`** — §4.6; maps to `POST /news {title, body}`.
- **Run record** — `{site, started, finished, items_found, new_items, errors}` in the state store (§4.9).
- **`CandidateResult`** — `{item_count, samples, schema_errors, exceptions, pages_fetched}` from the §6 scoring core (§4.11).
- **Trainset example** — `dspy.Example(site_url, corpus, render, hints, discovery_*, crawler_source).with_inputs("site_url","corpus","render","hints")`; inputs-only (§4.11).

## 6. Generation acceptance gates
A generated script is accepted only if it passes, in order:
1. **Static analysis** — AST allowlist; no banned imports/builtins; matches the §4.3 contract.
   Consumes `crawler_source.code` (the field is a `dspy.Code`, fences already stripped — §4.2).
2. **Runtime validation** — yields ≥ `min_items`; every item has non-empty `title`,
   `body`, and a valid on-domain `url`; runs within time/page/memory budget; obeys robots.
3. **Stability** — two runs overlap by **Jaccard of canonical URLs ≥ a configured threshold**
   (§4.6 canonicalizes `url`). A **hard** accept/reject gate, but *excluded from the scalar score*
   fed to optimizers (§4.11) — normal homepage churn would otherwise inject selection variance.
4. **Review gate** (§8) — for v1, default-on human approval before a script is enabled.

Gates 1–2 are computed by **one pure scoring core** that runs a candidate via `validate_crawler`,
returns a `CandidateResult` + a derived float score + a `gate_failures` reason string. Generation
wraps the RLM call in `dspy.BestOfN` (sample N, keep the best-scoring) or `dspy.Refine` (retry —
the 3.x replacement for the removed Assert/Suggest) over that score. The **same scoring core, not
the same callable**, also feeds the §4.11 optimizer through signature-correct adapters — the
`reward_fn(args, pred)`, `dspy.Evaluate`, and 5-arg GEPA metrics have incompatible signatures and
must not be conflated (§4.11).

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
      snapshot.py           #   SiteSnapshot(SandboxSerializable) (§9)
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

## 9. DSPy / runtime notes
- `dspy.RLM` is `@experimental` — pin DSPy `3.2.x` (already a dep of `bizup-news`).
- The **default RLM sandbox requires Deno** (Pyodide/WASM); provision it in `flake.nix`.
- RLM's Pyodide sandbox has **no network** — the model reasons over the prefetched corpus and
  reaches the live site only through the host **tools** in §4.2, never from inside the sandbox.
- **Two distinct sandboxes:** RLM's Pyodide REPL (where the model *reasons and drafts*) and the
  host execution sandbox (§4.4, where candidate and committed crawlers actually *run*). They
  never share a process; `validate_crawler` (§4.2) is the bridge.
- **`SiteSnapshot` contract:** subclass `dspy.SandboxSerializable` (nominal ABC — duck typing
  fails the `isinstance` check) and implement all four methods; `to_sandbox`/`sandbox_assignment`
  round-trip a *multi-part* payload (homepage, robots, sitemap, N articles) into named REPL
  variables, and `rlm_preview` surfaces **structure** (discovered feed/sitemap URLs + an HTML
  outline), not byte counts. Never pass megabyte HTML as a plain `str` field.
- **Interpreter left at default.** Keep RLM's `interpreter=` at the default Pyodide/Deno; a custom
  host-CPython interpreter was considered and rejected — it would run the model's *ungated*
  exploratory code in a network-capable process, dissolving the no-network isolation §8 relies on.
  The host execution sandbox (§4.4) is wired only as the *gated* `validate_crawler` tool.
- **No importable `MockInterpreter`.** DSPy ships none on the wheel; author one satisfying the
  `CodeInterpreter` protocol (copy/adapt `bizup-news`'s `tests/reports/_mock_interpreter.py`), and
  drive the planner with `DummyLM` (`from dspy.utils.dummies`) for Deno-free, model-free tests.
- Obtain the LM via `dspy.context`, not the thread-owned `dspy.configure`; build it `cache=False`
  for generation (§4.2). Use `aforward`/async to match the news project's style.

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
| `AUTOCRAWLER_MAX_ITERATIONS` | e.g. `25` | RLM root iteration cap (generation) |
| `AUTOCRAWLER_MAX_LLM_CALLS` | e.g. `60` | RLM sub-LLM call-count cap (generation) |
| `NEWS_API_URL` | `http://localhost:8000` | `bizup-news` base URL for `POST /news` |
| `AUTOCRAWLER_STATE_URL` | local Postgres/SQLite | crawl-state + health store |
| `AUTOCRAWLER_USER_AGENT` | identifying UA string | sent on every request |
| `AUTOCRAWLER_RATE_LIMIT` | e.g. `1/s/host` | politeness cap |
| `AUTOCRAWLER_MAX_PAGES` | e.g. `200` | per-crawl page budget |
| `AUTOCRAWLER_DEFAULT_SCHEDULE` | e.g. `30m` | crawl cadence for sites that set none |
| `AUTOCRAWLER_CONCURRENCY` | e.g. `4` | max sites crawled concurrently |
| `AUTOCRAWLER_REGEN_CHECK` | e.g. `24h` | how often the scheduler evaluates staleness |
| `AUTOCRAWLER_REOPT_CHECK` | e.g. `off` / `7d` | cadence of the offline self-improvement job (§4.11) |
| `AUTOCRAWLER_SOLVER` | `stealth \| flaresolverr \| none` | challenge-solver backend (§4.10) |
| `FLARESOLVERR_URL` | — | FlareSolverr endpoint when that backend is selected |
| `AUTOCRAWLER_CLEARANCE_TTL` | e.g. `30m` | how long a cached `cf_clearance` cookie is reused |
| `AUTOCRAWLER_EGRESS_PROXY` | — | shared egress proxy for solver + cheap requests (IP/UA pinning) |
| `AUTOCRAWLER_REVIEW_GATE` | `on` | require approval before enabling a generated script |

## 12. Testing strategy
- **Generator** — `dspy.RLM` driven by an autocrawler-authored `MockInterpreter` (dspy ships none
  importable; queued REPL responses) + `DummyLM`; assert tool wiring, that the scoring core and its
  three adapters score / feed back correctly, that a forced extraction (`final_reasoning`) is
  caught, and that bad output is rejected. No Deno, no live network.
- **Self-improvement (§4.11)** — `dspy.Evaluate` over frozen `SiteSnapshot`s + recorded fixtures;
  assert the held-out promotion gate refuses a non-improving candidate and that `save`/`load` JSON
  round-trips the generator program.
- **Runner/contract** — golden HTML/feed fixtures + recorded HTTP; assert normalized items
  and dedup behavior. A couple of hand-written reference crawlers double as contract tests.
- **Politeness/SSRF** — unit tests proving robots/rate-limit/allowed-host enforcement.
- Mirror `tests/` to `src/`, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), `ty`, `ruff`.

## 13. Milestones
- **M0 — Path proof:** contracts + runner + one *hand-written* crawler + `POST /news`. No LLM.
- **M1 — Generator:** `dspy.RLM` + host tools + `validate_crawler` loop; generate scripts for a seed list; acceptance gates (§6).
- **M2 — Scheduler & robustness:** the `autocrawl run` scheduler (due-selection, concurrency, retry/backoff) + run history; dedup/state, politeness, headless tier, Cloudflare challenge tier (§4.10), SSRF/sandbox hardening.
- **M3 — Self-healing:** health tracking + stale detection + scheduler-driven regeneration with review gate.
- **M4 — Operate:** metrics/observability, optional status/control HTTP surface.
- **M5 — Self-improving generator (§4.11), core:** the offline GEPA loop — `CrawlerGenerator(dspy.Module)`,
  the scoring-core adapters, the inputs-only trainset, the held-out promotion gate, and JSON save/load.
  **Not optional** — built after the M0–M4 path works (v1 has no optimized state to persist). GEPA is
  justified by *feedback-richness*, not low data; the payoff is better regeneration under drift and
  better cold-start on unseen sites (validated on a held-out split), not a monotonically improving
  fleet. Both GEPA and RLM are `@experimental`; `dspy.CodeAct` is the fallback if the owned predictor
  optimizes unevenly.

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
7. **Optimized-program round-trip** — confirm the chosen generator module (RLM, or the CodeAct
   fallback) actually persists/reloads through DSPy's JSON `save`/`load`; the RLM skill documents
   only `forward`/`aforward`/`.tools`. Validate before relying on §4.11 persistence.
