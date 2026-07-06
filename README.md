# jobcrawler

A self-maintaining **job-position tracker fleet**. For each careers board in a
checked-in registry, an `dspy.RLM` agent **generates a standalone Python crawler**
once (using a live LM, a sandboxed code interpreter, and real fetches), gates it
hard, and commits it. From then on the committed crawler runs on a schedule with
**no LLM in the crawl loop** — it just fetches the board and yields raw `Job`
records into a local positions store, which tracks each opening's lifecycle
(new → open → closed) over time. When a board's layout drifts and its crawler goes
dark, the daemon **self-heals** by regenerating it; an offline optimizer periodically
improves the *generator* itself.

There is exactly **one** new LLM surface, and it lives **outside** the crawl loop:
a host-side **profile matcher** that scores each new opening against your
[`PROFILE.md`](PROFILE.md) and notifies you of the strong fits. It is batched,
content-addressed-cached, daily-capped, and rate-paced, so the token bill stays
bounded and a job is scored *at most once* per (job, profile, model, prompt).

The design is captured in the internal §-numbered spec; this README is the operator-
and developer-facing summary. For running it as a service, see [`deploy/`](deploy/).

## How it works

```
 sites.yaml ─► generate ─► gate (§6) ─► commit crawlers/<slug>.py
                (RLM)        │                    │
                LM+Deno      │ AST · must-observe │ scheduler (§4.9)
                +fetch       │ completeness ·     │ + recheck sweep
                            ▼  two-run stability  ▼
                     trainset.jsonl            crawl ─── subprocess isolation
                        (§4.11)                (no LLM)    rlimits + timeout (§4.4)
                            │                     │
                    optimize (offline)            ▼
                    GEPA ─► generator-N.json   validate Job · dedup by content_hash
                            ┊                     │  → upsert positions (§4.7)
             adopt if it clears held-out margin   ▼
                                           lifecycle: open ↔ closed
                                        full-enum diff · recheck · expiry
                                                  │  (new / changed positions only)
                    self-heal (§4.8)              ▼
                 went dark? ─► reprobe ─► regen  enrich (§4.6, deterministic):
                         ▲                       discipline·level·workplace·salary
                         ┊                          │
                         └─ health signals ◄─       ▼
                                        prefilter ─► LLM match vs PROFILE.md ◄─ PROFILE.md
                                        (drop        (host-side · cache/batch/cap · §4.6)
                                         definite         │
                                         mismatch)        ▼
                                                   notify strong matches
                                                   (score ≥ MATCH_MIN_SCORE)
```

Key invariants:

- **No LLM in the crawl loop.** Generation is a one-time (or on-drift) cost; the
  committed crawler is plain Python. A crawl never calls a model or invokes Deno. The
  only LLM surface, the profile matcher, runs **host-side, outside the sandbox** — it
  reads the operator's `PROFILE.md` plus compact job digests, never the crawler.
- **Matching is at-most-once per `(job, profile, model, prompt)`.** The match cache is
  keyed on the job's `content_hash` (title/company/location/description), the
  `profile_hash`, the model, and the prompt version. A re-crawl of an unchanged
  posting, a daemon restart, a §4.9 recheck, or a pending-drain all hit the cache for
  **free**. Editing `PROFILE.md` changes `profile_hash` and *lazily* invalidates prior
  verdicts — only jobs that are actually re-examined pay again, never the whole backlog.
- **A broken crawl never closes positions.** Closure fires only when a source
  enumerates its whole board (`enumerates_all: true`), the crawl raised **no**
  exception, and it yielded **≥ 1** valid position — and even then a role must be
  *absent for `CLOSE_AFTER_MISSES` consecutive* healthy crawls before it flips to
  `closed` (the §4.7 mass-closure guard). A failed or empty run advances nothing.
- **Host-side gates & thresholds, hidden from the generator (§8).** Every threshold
  (`MIN_ITEMS`, description quality, stability, enum floor, the match cap/score/rate)
  lives in `config.py` and is applied host-side. The generator never sees them, so it
  can't overfit to them.
- **Committed crawlers run sandboxed (§4.4).** Each runs in an isolated subprocess
  under `RLIMIT_AS` / `RLIMIT_CPU` + a wall-clock timeout, so a runaway or
  memory-hungry crawler is bounded and reported as a failed run — it can't wedge or
  OOM the daemon and take the fleet down.
- **Self-heal delivery is policy-gated (§4.8).** Under the default `auto`
  `JOBCRAWLER_REVIEW_GATE`, a regenerated crawler is backed up to `_history/` then
  promoted live (safety = the §6 gates + `rollback`); set `on` to *stage* it to
  `_pending/` for a human `promote` instead.

## PROFILE.md — what you want

`PROFILE.md` (repo root, committed) is the single source of *what you're looking for*.
It has two deliberately-separated halves so the cheap deterministic layer and the
expensive LLM layer each read only the part they need:

- **YAML frontmatter** (between `---` fences) — machine-readable preferences the
  deterministic `prefilter` reads to drop **definite** mismatches *before* any token
  is spent. Every key is optional; an empty/omitted key means "no constraint"
  (recall-safe — the LLM still judges it). Enum-typed keys are validated at load time
  against the §4.6 classification vocabulary, so a typo fails loud (naming the bad
  value) rather than silently matching nothing.
- **free markdown body** — the rich narrative (skills, domains, seniority, what to
  reject) handed **verbatim** to the matcher LM as context. Prose the prefilter can't
  encode lives here.

```markdown
---
disciplines:            # normalize.py enums; omit a key for "no constraint"
  - backend             #   backend | frontend | fullstack | mobile | devops_sre |
  - fullstack           #   data | ml_ai | security | qa | embedded | eng_other | non_eng
levels:
  - senior              #   intern | junior | mid | senior | staff_plus |
  - staff_plus          #   tech_lead | manager | director_plus | unspecified
  - tech_lead
workplace:
  - remote              #   remote | hybrid | onsite | unspecified
locations:
  - São Paulo, Brazil
languages:
  - en
  - pt
min_salary:             # optional; compared against the job's *parsed* salary
  amount: 150000
  currency: USD
  period: year          #   year | month | hour
exclude_keywords:
  - clearance required
exclude_companies: []
---

# Jane Doe — Senior/Staff Backend Engineer

A decade on distributed systems, payments infrastructure, and protocol-level
backend work... (skills, domains that score high, what to reject — the free body
is the matcher's context, so be specific about the roles you *do* and *don't* want.)
```

**Edit → lazy re-score.** The whole file is content-addressed by a sha256 of its
normalized text (so a CRLF flip or trailing space is not a change). Because that hash
is one of the four match-cache keys, **editing the profile — frontmatter *or* body —
invalidates every prior verdict lazily**: new, changed, and pending positions re-score
against the new profile automatically the next time they're examined, while
already-scored open positions keep their old verdict until they're re-crawled or you
force it. To re-score the standing open set against the current profile *now*, run
`jobcrawl rematch`.

**Prefilter is recall-safe.** It drops only unambiguous rejects — a non-engineering
role, a *known* discipline/level the profile excludes, an `onsite` role for a
`remote`-only profile, an excluded keyword in the title, an excluded company. The
ambiguous `eng_other` / `unspecified` buckets and anything the profile is silent about
**pass** to the LLM — the model is cheap relative to a missed dream job.

If `PROFILE.md` is missing, the matcher raises a loud create-me hint rather than
silently scoring against an empty preference set.

## Quick start (development)

```sh
# Enter the pinned toolchain (Python 3.13, dspy 3.2.1, Deno, treefmt, uv).
nix develop

# Configure the LM (see "Configuration" for the full surface).
export OPENROUTER_API_KEY=sk-or-...            # read by litellm via the model slug
export JOBCRAWLER_MODEL=openrouter/<vendor>/<strong-code-model>   # RLM planner
export JOBCRAWLER_SUB_MODEL=openrouter/<vendor>/<cheap-code-model> # RLM sub_lm + match fallback
# Optional: a dedicated match model (else the cheap sub_lm is reused).
export JOBCRAWLER_MATCH_MODEL=openrouter/<vendor>/<cheap-model>

# — or, instead of an OpenRouter key, bill against a ChatGPT subscription (§9).
# Any slug above may be chatgpt/<model>; the token comes from the official Codex CLI:
#   codex login                                  # writes ~/.codex/auth.json (browser flow)
#   jobcrawl login                               # import that token into jobcrawler's store
#   export JOBCRAWLER_MODEL=chatgpt/gpt-5-codex  # (and/or SUB_MODEL / MATCH_MODEL)

# Describe what you're looking for (the matcher scores every job against this).
$EDITOR PROFILE.md                             # YAML frontmatter + markdown body

# Add a careers board to sites.yaml, e.g. a Greenhouse board:
#   - slug: acme
#     name: Acme
#     url: https://boards.greenhouse.io/acme
#     company: Acme                 # single-company board → hard-coded on every Job
#     hints: Greenhouse board token 'acme'; boards-api.greenhouse.io/v1/boards/acme/jobs?content=true

# Generate a crawler for one board, dry-run it (no scoring), then crawl + track.
jobcrawl generate acme
jobcrawl validate acme
jobcrawl crawl acme

# See what matched your profile, then run the self-scheduling daemon.
jobcrawl matches
jobcrawl run
```

The site registry is `sites.yaml` (`JOBCRAWLER_SITES`); its schema is documented
inline in [`deploy/sites.example.yaml`](deploy/sites.example.yaml).

## CLI reference

`jobcrawl <command>` — every command reads its configuration from the environment.

| Command | What it does |
| --- | --- |
| `run` | Start the scheduler daemon (§4.9): paces per-board crawls, runs the recheck/expiry sweep, the self-heal and regen timers, scores new positions against `PROFILE.md`, and drains gracefully on SIGINT/SIGTERM. The primary production entrypoint. |
| `generate <slug>` / `--all` / `--stale` | Run the RLM, gate the winner, and write `crawlers/<slug>.py`. Needs a configured LM + Deno + live fetch. `--stale` is driven by the daemon's health loop, not run by hand. |
| `validate <slug>` | Dry-run a committed crawler in the §4.4 sandbox and report position count + schema errors. **No tracking, no scoring.** |
| `crawl <slug>` / `--all` | Run committed crawlers, dedup, upsert into the positions store, enrich, prefilter, and score new/changed positions against the profile. |
| `matches` | List open positions the matcher scored at or above the notify threshold (`MATCH_MIN_SCORE`), strongest first — the "what should I apply to" view. |
| `positions` | List tracked positions with their lifecycle state (`open`/`closed`) and derived discipline/level/workplace — the raw board inventory the fleet is maintaining. |
| `rematch` | Re-score standing open positions against the *current* `PROFILE.md` now, and drain any `match_pending` backlog — the manual companion to the automatic lazy re-score after a profile edit. |
| `profile` | Load and validate `PROFILE.md`, printing the parsed prefs + profile hash (or the loud error naming a malformed/unknown value). A pre-flight for a profile edit. |
| `promote <slug>` | Promote a staged self-heal crawler (`_pending/<slug>.py`) to live (the human half of the §4.8 `on` review gate). |
| `rollback <slug>` | Revert a board to its prior crawler from its `_history/` backup — the undo for an `auto` self-heal promotion (§4.8). |
| `list` | One line per board: enabled flag, committed-script presence, last position count, health, and the §4.9 learned crawl cadence. |
| `status` | Observability read: per-board run aggregates, the fleet health distribution, position-lifecycle + match tallies, drift-recovery MTTR, the active generator, and the autonomy brake. Read-only. |
| `optimize` `[--adopt]` | Offline §4.11 self-improvement: build the trainset, optimize the generator with GEPA, write a versioned `generator-N.json`. `--adopt` flips the active-generator pointer on a clearing result. |
| `rollback-generator` | Undo the last generator adoption (flip the active-generator pointer back to the prior version, or baseline). Committed crawlers are unaffected. |
| `login` | Import the ChatGPT-subscription (Codex) OAuth token from `codex login` into jobcrawler's store, so a `chatgpt/*` model slug bills against the subscription (§9). One-shot; run once after `codex login` (and again if the token is revoked). |

`list` / `matches` output (read by an operator or a healthcheck):

```
$ jobcrawl list
acme                     on  script    last_positions=  24 health=ok       every=4h

$ jobcrawl matches
acme  https://boards.greenhouse.io/acme/jobs/123  score=88 strong  Staff Backend Engineer — Acme (remote)
acme  https://boards.greenhouse.io/acme/jobs/145  score=74 good    Senior Platform Engineer — Acme (remote)
```

## Configuration

Every knob is an environment variable read in [`config.py`](src/jobcrawler/config.py),
where each getter carries its rationale. Only the three in **Required** must be set;
everything else defaults sanely. `deploy/jobcrawler.env.example` is a ready-to-edit
starting point.

> **Removed in the jobs pivot:** the news-era `NEWS_API_URL`, `JOBCRAWLER_INGEST_RATE`,
> `JOBCRAWLER_INGEST_TIMEOUT`, and `JOBCRAWLER_PAYWALL_MARKERS` are **gone** — positions
> are tracked in the local state store, not POSTed to an external ingest API, and the
> paywall gate is replaced by `JOBCRAWLER_EXPIRED_MARKERS` (below). `MIN_BODY_CHARS` /
> `MIN_BODY_QUALITY` / `BODY_QUALITY_FLOOR` were renamed to their `DESCRIPTION` forms.

### Required

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Provider key, read by litellm via the model slug's `openrouter/` prefix. Use `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` for those providers. Needed by `generate`/`optimize` and by matching (`run`/`crawl`); the crawl itself needs no LM. **Not needed if every configured slug is `chatgpt/*`** — those authenticate with the Codex OAuth token from `jobcrawl login` (see *ChatGPT subscription* below), not an API key. |
| `JOBCRAWLER_MODEL` | — | RLM root planner — a strong, code-capable model. No default (errors loud rather than 404-ing deep in the provider). |
| `JOBCRAWLER_SUB_MODEL` | — | RLM `sub_lm` — a cheaper code model for in-sandbox `llm_query`. Required so it can't silently bill at the root rate. Also the **default match model** when `JOBCRAWLER_MATCH_MODEL` is unset. |

Matching needs a *cheap* model (`JOBCRAWLER_MATCH_MODEL`, else `JOBCRAWLER_SUB_MODEL`) —
never the root planner. With neither set, positions are still tracked and enriched, just
not scored.

### Profile & matching (§4.6) — *the one LLM surface, host-side*

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_PROFILE` | `PROFILE.md` | Path to the YAML-frontmatter + markdown profile every job is scored against. Missing ⇒ a loud create-me hint (no profile ⇒ nothing to match). |
| `JOBCRAWLER_MATCH_MODEL` | unset | The match LM. Optional: unset, matching borrows the cheap `SUB_MODEL` — never the root planner, whose per-token rate would make scoring the daily fleet ruinous. |
| `JOBCRAWLER_MATCH_BATCH` | `10` | Jobs per single `dspy.Predict(MatchJobs)` call. Batching amortizes the profile+rubric prompt prefix; too large and one malformed batch strands more jobs to `pending` on a length mismatch. |
| `JOBCRAWLER_MATCH_MIN_SCORE` | `70` | The 0–100 score at/above which a match is worth a `match` notification. Purely the notify/`matches` threshold — a 60 still lands in the store, it just doesn't ping. |
| `JOBCRAWLER_MATCH_DAILY_CAP` | `400` | Hard ceiling on jobs sent to the match LM per UTC day (the token backstop). **Cache hits are free** and never charged. Jobs past the cap are parked `match_pending`, never dropped — a later run drains them. |
| `JOBCRAWLER_MATCH_RATE` | `10/m` | Governor pacing for the match LM calls (`requests/interval`) so a big new-position sweep can't burst the provider into rate-limit errors. Paces *calls*; the daily cap bounds *volume*. |
| `JOBCRAWLER_FIRST_CRAWL_CAP` | `30` | On a board's first position-yielding crawl, only the newest `cap` jobs (by `posted_at`, discovery order as fallback) go to the matcher now; the backlog tail is parked `pending`, so a fresh board can't burn the whole daily cap on stale standing roles. |

### Models (optional)

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_REFLECTION_MODEL` | unset | GEPA reflection LM for `optimize`; ideally a strong model from a different family than `JOBCRAWLER_MODEL`. |
| `JOBCRAWLER_AUTH_DIR` | `~/.config/jobcrawler/chatgpt` | Where `jobcrawl login` stores the ChatGPT-subscription OAuth token (a token store, not an API key — keep it `chmod 600`). litellm reads and refreshes it there. Falls back to litellm's own `CHATGPT_TOKEN_DIR`. |

**ChatGPT subscription (§9).** Any model slug may be `chatgpt/<model>` (e.g. `chatgpt/gpt-5-codex`)
to bill LM calls against a ChatGPT Plus/Pro subscription via the Codex *Responses* backend instead of
per-token OpenRouter credits. jobcrawler writes no OAuth flow of its own: run the official `codex login`
once (it does the browser flow and writes `~/.codex/auth.json`), then `jobcrawl login` imports that token
into `JOBCRAWLER_AUTH_DIR`; litellm refreshes it from there. `run`/`crawl` fail fast at startup with a
`codex login` hint if a `chatgpt/*` slug has no usable token (they never block on an interactive prompt).
Note that this backend **prepends the Codex coding-agent system prompt** to every request — expected for
crawler generation, and tolerated for matching via the explicit output contract.

### Registry, state & artifacts

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_SITES` | `sites.yaml` | The board registry (§4.1). |
| `JOBCRAWLER_STATE_URL` | `sqlite:///jobcrawler.db` | Run-history, health, **positions**, and match-cache/spend store. |
| `JOBCRAWLER_CRAWLERS_DIR` | in-package `crawlers/` | Committed crawlers + `_pending` staging + `_history` backups. **Relocate to a writable, persistent path in production** (the package dir is read-only on native Nix and ephemeral in a container). |
| `JOBCRAWLER_GENERATOR_DIR` | `generators` | Where `optimize` writes versioned `generator-N.json`. |
| `JOBCRAWLER_GENERATOR_PATH` | unset | *Fallback* pointer to the active `generator.json` (resolution: CLI arg › `ACTIVE` pointer file › this env var › baseline). The autonomous loop adopts/rolls back via the `ACTIVE` pointer with no restart; roll back with `jobcrawl rollback-generator`. |
| `JOBCRAWLER_TRAINSET_PATH` | `trainset.jsonl` | §4.11 trainset (one row per admitted generation / recorded failure). |

### Generation caps (§4.2)

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `JOBCRAWLER_MAX_ITERATIONS` | `20` | `JOBCRAWLER_FANOUT_N` | `4` |
| `JOBCRAWLER_MAX_LLM_CALLS` | `40` | `JOBCRAWLER_MAX_TOKENS` | `16000` |
| `JOBCRAWLER_MAX_SUBCALLS` | `60` | `JOBCRAWLER_CORPUS_HOMEPAGE_CHARS` | `60000` |
| `JOBCRAWLER_FANOUT_SPEND_CAP` | `$8/site` | `JOBCRAWLER_CORPUS_ARTICLE_CHARS` | `20000` |

### Crawl & politeness (§4.5)

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `JOBCRAWLER_RATE_LIMIT` | `1/s/host` | `JOBCRAWLER_FETCH_RETRIES` | `2` |
| `JOBCRAWLER_MAX_PAGES` | `200` | `JOBCRAWLER_FETCH_BACKOFF` | `0.5` |
| `JOBCRAWLER_USER_AGENT` | `jobcrawler/0.1 (+job tracker)` | `JOBCRAWLER_FETCH_RETRY_CAP` | `30.0` |
| `JOBCRAWLER_FETCH_TIMEOUT` | `20.0` | `JOBCRAWLER_TRACKING_PARAM_DENYLIST` | built-in (`,`-sep) |

### Crawl isolation (§4.4)

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_CRAWL_TIMEOUT` | `600.0` | Wall-clock ceiling per committed crawl (bounds a *hung* crawl). |
| `JOBCRAWLER_CRAWL_CPU_SECONDS` | `120.0` | `RLIMIT_CPU` — kills a compute spin (decoupled from wall clock). |
| `JOBCRAWLER_CRAWL_MEM_MB` | `1024` | `RLIMIT_AS` — caps a memory blowup. |

### Gate thresholds (§6) — *never seen by the generator*

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_MIN_ITEMS` | `1` | Minimum positions a crawl must yield to clear the completeness gate. Dropped from the news-era `5`: a two-role startup careers page is a legitimate, complete board. Completeness is instead carried by the enum floor + stability gates. |
| `JOBCRAWLER_MIN_DESCRIPTION_CHARS` | `300` | Length floor a job description must clear to count as complete (renamed from `MIN_BODY_CHARS`, down from `400`). A title-only stub or a "see our site" redirect fails; a real posting passes. |
| `JOBCRAWLER_MIN_DESCRIPTION_QUALITY` | `0.6` | Fraction of sampled descriptions that must clear the length floor for the gate to pass (renamed from `MIN_BODY_QUALITY`). |
| `JOBCRAWLER_STABILITY_JACCARD` | `0.6` | Two-run overlap floor for the stability gate. |
| `JOBCRAWLER_ENUM_FLOOR_RATIO` | `0.5` | Did the crawl get roughly as many positions as the board advertises? |

### Completeness & freshness (§4.6) — *host-side, at ingest*

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_MIN_UNIQUE_DESCRIPTION_CHARS` | `150` | Minimum *distinct* characters a description must contain — catches boilerplate padding that clears the length floor (a repeated blurb, an ATS placeholder). Conservative: a borderline-real posting is kept. |
| `JOBCRAWLER_EXPIRED_MARKERS` | 5 built-in (`\|`-sep; empty disables) | Closed-posting substrings (EN + pt-BR: *no longer accepting applications*, *this position has been filled*, *position is no longer available*, *vaga encerrada*, *vaga indisponível*) that drop a job as `expired` even while the board still serves the page — the still-present-but-dead case the enumerate-diff can't catch. Mirrors the old paywall-marker pattern. |

### Position lifecycle: closure & recheck (§4.7 / §4.9) — *host-side*

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_CLOSE_AFTER_MISSES` | `2` | Consecutive healthy full-enumeration crawls a position must be **absent** from before it flips to `closed`. A single miss is often a transient hiccup (a dropped page, an ATS 500); the second confirms it. Only enumerating boards diff this way, and only after a successful non-empty crawl (the mass-closure guard). |
| `JOBCRAWLER_RECHECK_INTERVAL` | `12h` | How often the recheck loop re-fetches standing open positions to confirm they're still live. The **only** closure signal for non-enumerating sources (`enumerates_all: false` — RSS / latest-N feeds); also re-verifies enumerating rows against `EXPIRED_MARKERS` / `expires_at`. |
| `JOBCRAWLER_RECHECK_CAP` | `150` | Max standing positions the recheck loop re-fetches per pass (oldest-checked first), so a fleet with thousands of open rows doesn't starve fresh crawls. |

### Scheduler & adaptive cadence (§4.9)

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_DEFAULT_SCHEDULE` | `4h` | Fixed per-board cadence when a board sets none and isn't adaptive. Up from the news-era `30m`: job boards move on the order of days, so 4h catches a new posting the day it lands while staying polite. |
| `JOBCRAWLER_CONCURRENCY` | `4` | Max simultaneous crawls. |
| `JOBCRAWLER_ADAPTIVE_DEFAULT` | `true` | A board with no explicit `schedule` self-tunes its cadence to its hit-rate instead of the fixed `DEFAULT_SCHEDULE`; pin `schedule:` or `adaptive: false` on a board to opt out. |
| `JOBCRAWLER_MIN_SCHEDULE` | `1h` | Politeness floor for an adaptive interval (up from `15m`) — job postings don't churn minute-to-minute. |
| `JOBCRAWLER_MAX_SCHEDULE` | `3d` | Staleness ceiling for an adaptive interval (up from `1d`) — a job typically stays open for weeks. |
| `JOBCRAWLER_ADAPTIVE_TARGET_N` | `2` | Target checks-per-post: an AIMD controller on the fraction of recent crawls finding ≥1 new position steers toward a `1/N` hit rate, clamped into the `MIN…MAX_SCHEDULE` band (`N` clamped ≥ 1). |
| `JOBCRAWLER_ADAPTIVE_WINDOW` | `8` | How many recent crawls the controller averages the hit rate over. |

The learned per-board interval shows in `jobcrawl list` as `every=…` (or `every=adaptive`
before one is learned). The controller adjusts only *timing*, never code, and so is **not**
governed by the autonomy brake below.

### Self-heal (§4.8)

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_REGEN_CHECK` | `24h` | How often the periodic self-heal timer assesses drift fleet-wide. |
| `JOBCRAWLER_HEAL_COOLDOWN` | `6h` | Minimum spacing between *event-driven* heals of one board (a blacked-out or quality-collapsed crawl triggers an immediate assessment). `off` disables the fast path, leaving only the periodic sweep. |
| `JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR` | `0.5` | Share of complete-description positions below which a crawl (that still found positions) is a partial-drift signal — catches a board quietly switching to title-only stubs (renamed from `BODY_QUALITY_FLOOR`). Host-side only (§8); clamped to (0, 1]. |
| `JOBCRAWLER_GENFAIL_BACKOFF` | `6h` | Base delay before a generation-failed board is retried with the current generator; doubles per consecutive failure. |
| `JOBCRAWLER_GENFAIL_BACKOFF_CAP` | `7d` | Ceiling for that geometric retry backoff — a chronically broken board backs off without ever being abandoned. |
| `JOBCRAWLER_REVIEW_GATE` | `auto` | Tri-state self-heal & cold-start delivery: **`auto`** (default) auto-promotes a healed/new crawler after backing the live one up to `_history/<slug>/` (safety = §6 gates + `rollback`, not a human); **`on`** stages it to `_pending/<slug>.py` for an operator `promote`; **`off`** writes live with no backup. Legacy booleans still parse (truthy ⇒ `on`, falsy ⇒ `off`). |

### Self-improvement (§4.11) — *offline, out-of-band*

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_REOPT_CHECK` | `24h` | Cadence of the GEPA self-improvement timer (`off` disables). A check dispatches `jobcrawl optimize` **detached** and only when the trainset/pool are ready, so a daily check is not a daily compile. |
| `JOBCRAWLER_TRAINSET_STABLE_RUNS` | `3` | Stable runs before a generation is trainset-admissible. |
| `JOBCRAWLER_PROMOTION_EVAL_PASSES` | `3` | Independent held-out eval passes averaged before a promotion/adoption decision (averages out the stochastic generator's cross-run noise; `1` = single-pass, cheaper/noisier). |
| `JOBCRAWLER_OPTIMIZE_LOCK` | `optimize.lock` | Single-flight lockfile held for an optimize run, so a reopt tick mid-compile is a no-op. CWD-relative. |
| `JOBCRAWLER_OPTIMIZE_LOG` | `optimize.log` | Where the detached optimize subprocess appends stdout/stderr (it outlives the daemon). CWD-relative. |
| `JOBCRAWLER_ADOPT_PROBATION` | `48h` | Probation window after an auto-adoption before the regression watcher judges it; `off` disables the watcher (the held-out margin gate is then the only protection). |
| `JOBCRAWLER_ADOPT_REGRESSION_DELTA` | `2` | How many *more* boards must newly enter `generation_failed` in the window after an adoption than in the equal window before it for the watcher to roll the pointer back (floored at 1). |

A newly-optimized generator that clears the frozen held-out margin is **auto-adopted** (the
active-generator pointer flips with no restart); the watcher above is the paired safety net.
Undo a bad adoption by hand with `jobcrawl rollback-generator`.

### Autonomy & notifications (§8)

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_AUTONOMY` | `on` | Master kill-switch over **every** self-modifying action — periodic + event-driven self-heal, auto-promotion, the reopt dispatch, and auto-adoption. `off` freezes all of them (the daemon still crawls, **tracks positions, rechecks, and matches**, and records run/health signals); operator commands (`promote`, `rollback`, `rollback-generator`, `optimize --adopt`) stay available. Read at scheduler start — flip and restart to apply. |
| `JOBCRAWLER_NOTIFY_WEBHOOK` | unset | `http(s)` URL sent a small JSON payload on every autonomous action *and* on each strong `match`, in addition to the log line. Best-effort and fire-and-forget. Unset ⇒ log only. |
| `JOBCRAWLER_NOTIFY_TIMEOUT` | `10.0` | Per-POST timeout (seconds) for the notify webhook. |

### Solver (§4.10) & logging

| Variable | Default | Notes |
| --- | --- | --- |
| `JOBCRAWLER_SOLVER` | `none` | Challenge-solver backend. `flaresolverr` is the opt-in backend. |
| `FLARESOLVERR_URL` | unset | Required when `JOBCRAWLER_SOLVER=flaresolverr`. |
| `JOBCRAWLER_CLEARANCE_TTL` | `30m` | Cached-clearance lifetime. |
| `JOBCRAWLER_EGRESS_PROXY` | unset | Optional egress proxy for fetches. |
| `JOBCRAWLER_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |

## Operator runbook

**Add a board.** Append an entry to `sites.yaml` (schema in
`deploy/sites.example.yaml`) — that's all that's required. When the running daemon finds
an enabled board with no crawler, it writes the first one itself (§4.2 cold-start),
honoring `JOBCRAWLER_REVIEW_GATE`. Two optional per-board fields matter for job boards:

- `company:` — on a **single-company board** (a Greenhouse/Lever/Ashby/Gupy token), the
  generator hard-codes this on every `Job`, since the board itself doesn't repeat it.
- `enumerates_all:` — defaults `true`. Set it **`false`** for a source that only shows a
  recent window (an RSS feed, RemoteOK's latest-100): the full-enumeration closure diff
  is unsound there and is skipped — the §4.9 recheck loop is the closure signal instead.

Put the ATS shape in `hints:` so the generator seeds the right endpoint (facts from live
probes, 2026-07-02):

| ATS | Hint pattern |
| --- | --- |
| **Greenhouse** | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` — whole board in one call; `content` is the full description but HTML-entity-escaped (unescape + strip tags); no salary/employment fields; `location.name` is one flattened string. |
| **Lever** | `api.lever.co/v0/postings/<token>?mode=json` — `description` is the intro only; full text = `description` + `lists[].content` + `additional`; `workplaceType` always present; `categories.{location,team,commitment}`; `createdAt` is epoch-ms; no company field. |
| **Ashby** | `api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true` — `descriptionPlain/Html` are full; `employmentType` always; structured `compensation` ~70% of the time; `isRemote` means "remote allowed" — prefer `workplaceType`; boards can be MBs. |
| **Gupy** (BR) | `employability-portal.gupy.io/api/v1/jobs?companyId=..&limit=100&offset=..` paginates (limit max 100 — page until short, don't trust `total` at 100); `publishedDate` + `expiresAt`; salary absent; the `<company>.gupy.io` board also embeds all jobs in `__NEXT_DATA__`. |

To bring a board up by hand instead: `jobcrawl generate <slug>` → `jobcrawl validate
<slug>` → `jobcrawl crawl <slug>`. (With `JOBCRAWLER_AUTONOMY=off` the daemon won't
generate on its own — a crawler-less board stays idle until you generate it manually.)

**Watch the fleet.** `jobcrawl list` shows which boards have a committed script, their
last position count, and the learned cadence (`every=4h` / `every=adaptive` / `every=fixed`).
`jobcrawl status` shows run aggregates, the health distribution (`healthy` / `stale` /
`pending_review` / `generation_failed`), the position-lifecycle + match tallies
(`open`/`closed`/`matched`/`pending`), the drift-recovery MTTR, the active generator
version + held-out score, and whether the autonomy brake is `on`/`OFF`. `jobcrawl matches`
is the human-facing payoff — the open positions scored `≥ MATCH_MIN_SCORE`, strongest
first; `jobcrawl positions` is the raw tracked inventory.

**Tune matching spend.** The token bill is bounded by four knobs. `JOBCRAWLER_MATCH_DAILY_CAP`
(default 400) is the hard per-UTC-day ceiling on *LLM-scored* jobs — cache hits are free, so
steady-state re-crawls cost nothing and only genuinely-new/changed postings draw it down.
Overflow parks as `match_pending` and drains on later runs, never dropped, so a raise is
safe when you want faster coverage of a big backlog. `JOBCRAWLER_FIRST_CRAWL_CAP` (30) stops
a brand-new board's standing back-catalog from eating the whole daily cap at once.
`JOBCRAWLER_MATCH_RATE` (`10/m`) paces the calls so a sweep can't burst the provider;
`JOBCRAWLER_MATCH_MIN_SCORE` (70) is purely how selective the `matches`/notify surface is —
raise it for fewer, stronger alerts. Editing `PROFILE.md` re-scores lazily (only jobs
re-examined pay); `jobcrawl rematch` forces the standing open set through now, subject to
the same cap.

**Promote a self-healed crawler.** Behaviour depends on `JOBCRAWLER_REVIEW_GATE`
(default `auto`). With `auto`, the daemon promotes a regenerated crawler itself after
backing the live one up to `_history/<slug>/` — undo with `jobcrawl rollback <slug>`. Set
`on` to keep a human in the loop: the crawler is staged and `status` shows it
`pending_review`; inspect `<crawlers_dir>/_pending/<slug>.py`, then `jobcrawl promote
<slug>` to take it live. With `off`, it writes live with no backup.

**Improve the generator.** The daemon runs this itself: on the `JOBCRAWLER_REOPT_CHECK`
cadence (default `24h`) it dispatches `jobcrawl optimize` **out-of-band** (a detached
subprocess logging to `JOBCRAWLER_OPTIMIZE_LOG`, serialized by `JOBCRAWLER_OPTIMIZE_LOCK`)
to build the trainset and emit a new `generators/generator-N.json`; a result that clears the
frozen held-out margin is auto-adopted (pointer flips, no restart). Run `jobcrawl optimize`
by hand for an immediate pass, or set `JOBCRAWLER_REOPT_CHECK=off` to disable the timer.

**Roll back a bad generator.** `jobcrawl rollback-generator` undoes the last adoption —
restoring the previous `generator-N.json`, or baseline when the first-ever adoption is
undone. The §4.11 watcher does this automatically if an adoption regresses the fleet
within `JOBCRAWLER_ADOPT_PROBATION` (see `JOBCRAWLER_ADOPT_REGRESSION_DELTA`). Committed
crawlers are unaffected — only future generations change.

**Pull the brake.** `JOBCRAWLER_AUTONOMY=off` (then restart) freezes every
self-modifying action — self-heal, auto-promotion, the reopt dispatch, auto-adoption.
The daemon keeps **crawling, tracking positions, running the recheck/expiry sweep, and
scoring against your profile** — those are observation and bookkeeping, not
self-modification of code (adaptive cadence is timing, matching is scoring), so they stay
live. Operator commands (`promote`, `rollback`, `rollback-generator`, `optimize --adopt`)
stay available, so you can drive changes by hand with the daemon's own initiative stopped.

**Troubleshooting.**

- *`JOBCRAWLER_MODEL is required` / `…SUB_MODEL is required`* — set both model slugs;
  there is intentionally no default.
- *`no match model configured`* — matching needs `JOBCRAWLER_MATCH_MODEL` or (the fallback)
  `JOBCRAWLER_SUB_MODEL`; it never uses the root planner. Without one, positions are tracked
  but unscored.
- *`no Codex login found` / `token refresh failed`* (a `chatgpt/*` slug) — the ChatGPT-subscription
  OAuth token is missing or dead. Run `codex login` (the official Codex CLI) then `jobcrawl login` to
  import it. `run`/`crawl` preflight surfaces this at startup rather than blocking the daemon on an
  interactive prompt; a revoked token needs the same two commands again.
- *`no profile found at 'PROFILE.md'`* — the matcher can't score against nothing; create the
  file (frontmatter + body). A *malformed* profile is different: it raises a `ValueError`
  **naming the offending value** (an unknown discipline/level/workplace enum, a bad
  `min_salary` mapping, an unclosed `---` fence). Run `jobcrawl profile` to validate before
  a crawl.
- *Matches stop appearing mid-day* — the match daily cap is exhausted; new jobs are parked
  `match_pending` (see the `pending` tally in `status`) and scored on the next UTC day, or
  sooner if you raise `JOBCRAWLER_MATCH_DAILY_CAP` and run `jobcrawl rematch`. Nothing is
  dropped.
- *A board yields 0 positions after a site redesign* — the crawler drifted. The daemon's
  event-driven self-heal reprobes it (a real failure vs a transient blackout) and, if
  confirmed, regenerates it against the board's *current* HTML; the mass-closure guard means
  the board's standing positions are **not** closed on the failed crawl — they stay `open`
  until a healthy full-enumeration crawl (or the recheck loop) confirms them gone.
- *RemoteOK / a partial feed returns only ~100 rows* — that source doesn't enumerate its
  whole board; set `enumerates_all: false` so closure defers to the recheck loop, and note
  RemoteOK needs a browser `User-Agent` and its first `/api` element is a legal-notice object
  to skip. Set `JOBCRAWLER_USER_AGENT` accordingly for such boards.
- *Cloudflare-challenged board* — set `JOBCRAWLER_SOLVER=flaresolverr` +
  `FLARESOLVERR_URL`; the default `none` uses plain httpx.

## Development

Tooling and the TDD loop are pinned by the Nix flake:

```sh
nix develop -c pytest -q          # full suite (the RLM + match LM are mocked; no Deno, no network)
nix develop -c ty check           # type check (run before committing)
nix fmt                           # treefmt: nixfmt --strict + ruff-format
nix flake check                   # CI release gate: ty + the full pytest suite, hermetic
```

`nix build .#dockerImage` builds the deployable OCI image (see `deploy/`). dspy is
pinned to an exact version and guarded by a tripwire test
([`tests/test_dspy_pin.py`](tests/test_dspy_pin.py)): a bump fails CI until the
source-verified internals are re-checked (§9).

## Security notes

- The provider API key is read from the environment by litellm and never logged.
  Keep `jobcrawler.env` at mode `600`; rotate keys out of band.
- The generator runs untrusted, model-written code only inside the §4.2 Deno/Pyodide
  sandbox (generate/optimize) and the §4.4 subprocess sandbox (validate/crawl) — never
  in the daemon process. Host-side thresholds are never exposed to it (§8).
- The profile matcher is the one LLM in the running system, and it runs **host-side,
  outside every sandbox**. It reads only your `PROFILE.md` and compact per-job digests
  (structured fields + a 1200-char description head); its verdicts are surfaced to you
  and are **never** fed back to the generator (§8), so it can't leak a scoring signal the
  generator could overfit to.
