# bizup-autocrawler

A self-maintaining news crawler fleet. For each site in a checked-in registry, an
`dspy.RLM` agent **generates a standalone Python crawler** once (using a live LM, a
sandboxed code interpreter, and real fetches), gates it hard, and commits it. From
then on the committed crawler runs on a schedule with **no LLM in the loop** — it
just fetches, extracts, and POSTs articles to the [bizup-news](../news) ingest API.
When a site's layout drifts and its crawler goes dark, the daemon **self-heals** by
regenerating it; an offline optimizer periodically improves the *generator* itself.

The authoritative design is [`SPEC.md`](SPEC.md); this README is the operator- and
developer-facing summary. For running it as a service, see [`deploy/`](deploy/).

## How it works

```
 sites.yaml ──► generate ──► gate (§6) ──► commit crawlers/<slug>.py
                  (RLM)        │                     │
                  LM+Deno      │ AST · must-observe · │ scheduler (§4.9)
                  +fetch       │ completeness ·       ▼
                               │ two-run stability    crawl  ── subprocess
                               ▼                      (no LLM)   isolation (§4.4)
                       trainset.jsonl                   │        rlimits+timeout
                          (§4.11)                       ▼
                               │              dedup · completeness filter
                       optimize (offline)              │
                       GEPA ─► generator-N.json        ▼
                                              POST → bizup-news (§4.7)
                                                       │
                       self-heal (§4.8) ◄──── went dark? reprobe → regenerate
```

Key invariants:

- **No LLM in the crawl loop.** Generation is a one-time (or on-drift) cost; the
  committed crawler is plain Python. A crawl never calls a model or invokes Deno.
- **Host-side gates, hidden from the generator (§8).** Every threshold
  (`min_items`, body-quality, stability, enumeration floor) lives in `config.py` and
  is applied host-side. The generator never sees them, so it can't overfit to them.
- **Committed crawlers run sandboxed (§4.4).** Each runs in an isolated subprocess
  under `RLIMIT_AS` / `RLIMIT_CPU` + a wall-clock timeout, so a runaway or
  memory-hungry crawler is bounded and reported as a failed run — it can't wedge or
  OOM the daemon and take the fleet down.
- **Self-heal never silently replaces a live crawler (§14.2).** With the review gate
  on (default), a regenerated crawler is *staged* to `_pending/` for a human to
  `promote`; the daemon keeps running the known-good one until then.

## Quick start (development)

```sh
# Enter the pinned toolchain (Python 3.13, dspy 3.2.1, Deno, treefmt, uv).
nix develop

# Configure the LM + ingest target (see "Configuration" for the full surface).
export OPENROUTER_API_KEY=sk-or-...           # read by litellm via the model slug
export AUTOCRAWLER_MODEL=openrouter/<vendor>/<strong-code-model>
export AUTOCRAWLER_SUB_MODEL=openrouter/<vendor>/<cheap-code-model>
export NEWS_API_URL=http://localhost:8000     # the bizup-news ingest API

# Generate a crawler for one site, dry-run it (no ingest), then crawl + ingest.
autocrawl generate techcrunch
autocrawl validate techcrunch
autocrawl crawl techcrunch

# Or run the self-scheduling daemon over the whole registry.
autocrawl run
```

The site registry is `sites.yaml` (`AUTOCRAWLER_SITES`); its schema is documented
inline in [`deploy/sites.example.yaml`](deploy/sites.example.yaml).

## CLI reference

`autocrawl <command>` — every command reads its configuration from the environment.

| Command | What it does |
| --- | --- |
| `run` | Start the scheduler daemon (§4.9): paces per-site crawls, runs the self-heal and regen timers, drains gracefully on SIGINT/SIGTERM. The primary production entrypoint. |
| `generate <slug>` / `--all` / `--stale` | Run the RLM, gate the winner, and write `crawlers/<slug>.py`. Needs a configured LM + Deno + live fetch. `--stale` is driven by the daemon's health loop, not run by hand. |
| `validate <slug>` | Dry-run a committed crawler in the §4.4 sandbox and report item count + schema errors. **No ingest.** |
| `crawl <slug>` / `--all` | Run committed crawlers, dedup, completeness-filter, and POST to bizup-news. |
| `promote <slug>` | Promote a staged self-heal crawler (`_pending/<slug>.py`) to live (the human half of the §14.2 review gate). |
| `list` | One line per site: enabled flag, committed-script presence, last item count, health. |
| `status` | M4 observability: per-site run aggregates, the fleet health distribution, and observed drift-recovery MTTR. Read-only. |
| `optimize` | Offline §4.11 self-improvement: build the trainset, optimize the generator with GEPA, write a versioned `generator-N.json`. |

`list` / `status` output (read by an operator or a healthcheck):

```
$ autocrawl list
techcrunch               on  script    last_items=  18 health=ok

$ autocrawl status
techcrunch               health=healthy          runs=12 found=210 new=47 errors=0 err_rate=0.00 last=2026-06-21T18:30:00
fleet: healthy=1 stale=0 pending_review=0 generation_failed=0
drift recovery MTTR (observed): n/a
```

## Configuration

Every knob is an environment variable read in [`config.py`](src/bizup_autocrawler/config.py),
where each getter carries its rationale. Only the four in **Required** must be set;
everything else defaults sanely. `deploy/autocrawler.env.example` is a ready-to-edit
starting point.

### Required

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Provider key, read by litellm via the model slug's `openrouter/` prefix. Use `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` for those providers. Needed by `generate`/`optimize`, not `run`/`crawl`. |
| `AUTOCRAWLER_MODEL` | — | RLM root planner — a strong, code-capable model. No default (errors loud rather than 404-ing deep in the provider). |
| `AUTOCRAWLER_SUB_MODEL` | — | RLM `sub_lm` — a cheaper code model for in-sandbox `llm_query`. Required so it can't silently bill at the root rate. |
| `NEWS_API_URL` | `http://localhost:8000` | bizup-news ingest API. (Technically defaulted, but you must point it at a real news service; in a container it must not be `localhost` — see `deploy/`.) |

### Registry, state & artifacts

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTOCRAWLER_SITES` | `sites.yaml` | The site registry (§4.1). |
| `AUTOCRAWLER_STATE_URL` | `sqlite:///autocrawler.db` | Run-history + health state store. |
| `AUTOCRAWLER_CRAWLERS_DIR` | in-package `crawlers/` | Committed crawlers + `_pending` staging. **Relocate to a writable, persistent path in production** (the package dir is read-only on native Nix and ephemeral in a container). |
| `AUTOCRAWLER_GENERATOR_DIR` | `generators` | Where `optimize` writes versioned `generator-N.json`. |
| `AUTOCRAWLER_GENERATOR_PATH` | unset | Pointer to the active `generator.json`; unset boots the baseline. **Flipping this to a prior artifact is the rollback.** |
| `AUTOCRAWLER_TRAINSET_PATH` | `trainset.jsonl` | §4.11 trainset (one row per admitted generation / recorded failure). |

### Models (optional)

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTOCRAWLER_REFLECTION_MODEL` | unset | GEPA reflection LM for `optimize`; ideally a strong model from a different family than `AUTOCRAWLER_MODEL`. |

### Generation caps (§4.2)

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `AUTOCRAWLER_MAX_ITERATIONS` | `20` | `AUTOCRAWLER_FANOUT_N` | `4` |
| `AUTOCRAWLER_MAX_LLM_CALLS` | `40` | `AUTOCRAWLER_MAX_TOKENS` | `16000` |
| `AUTOCRAWLER_MAX_SUBCALLS` | `60` | `AUTOCRAWLER_CORPUS_HOMEPAGE_CHARS` | `60000` |
| `AUTOCRAWLER_FANOUT_SPEND_CAP` | `$8/site` | `AUTOCRAWLER_CORPUS_ARTICLE_CHARS` | `20000` |

### Ingest & politeness (§4.5 / §4.7)

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `AUTOCRAWLER_INGEST_RATE` | `2/s` | `AUTOCRAWLER_RATE_LIMIT` | `1/s/host` |
| `AUTOCRAWLER_INGEST_TIMEOUT` | `120.0` | `AUTOCRAWLER_MAX_PAGES` | `200` |
| `AUTOCRAWLER_FIRST_CRAWL_CAP` | `30` | `AUTOCRAWLER_FETCH_TIMEOUT` | `20.0` |
| `AUTOCRAWLER_USER_AGENT` | `bizup-autocrawler/0.1 …` | `AUTOCRAWLER_FETCH_RETRIES` | `2` |
| `AUTOCRAWLER_PAYWALL_MARKERS` | 2 built-in (`\|`-sep; empty disables) | `AUTOCRAWLER_FETCH_BACKOFF` | `0.5` |
| `AUTOCRAWLER_TRACKING_PARAM_DENYLIST` | built-in (`,`-sep) | `AUTOCRAWLER_FETCH_RETRY_CAP` | `30.0` |

### Crawl isolation (§4.4)

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTOCRAWLER_CRAWL_TIMEOUT` | `600.0` | Wall-clock ceiling per committed crawl (bounds a *hung* crawl). |
| `AUTOCRAWLER_CRAWL_CPU_SECONDS` | `120.0` | `RLIMIT_CPU` — kills a compute spin (decoupled from wall clock). |
| `AUTOCRAWLER_CRAWL_MEM_MB` | `1024` | `RLIMIT_AS` — caps a memory blowup. |

### Gate thresholds (§6) — *never seen by the generator*

| Variable | Default | Variable | Default |
| --- | --- | --- | --- |
| `AUTOCRAWLER_MIN_ITEMS` | `5` | `AUTOCRAWLER_STABILITY_JACCARD` | `0.6` |
| `AUTOCRAWLER_MIN_BODY_CHARS` | `400` | `AUTOCRAWLER_ENUM_FLOOR_RATIO` | `0.5` |
| `AUTOCRAWLER_MIN_BODY_QUALITY` | `0.6` | | |

### Scheduler (§4.9), review (§8), solver (§4.10), logging

| Variable | Default | Notes |
| --- | --- | --- |
| `AUTOCRAWLER_DEFAULT_SCHEDULE` | `30m` | Per-site cadence when a site sets none. |
| `AUTOCRAWLER_CONCURRENCY` | `4` | Max simultaneous crawls. |
| `AUTOCRAWLER_REGEN_CHECK` | `24h` | How often the self-heal timer assesses drift. |
| `AUTOCRAWLER_REOPT_CHECK` | `off` | Offline re-optimization cadence (`off` disables). |
| `AUTOCRAWLER_TRAINSET_STABLE_RUNS` | `3` | Stable runs before a generation is trainset-admissible. |
| `AUTOCRAWLER_REVIEW_GATE` | `true` | On: self-heal stages to `_pending` for human `promote`. Off: auto-heal goes live. |
| `AUTOCRAWLER_SOLVER` | `none` | Challenge-solver backend (§4.10). `flaresolverr` is the opt-in backend. |
| `FLARESOLVERR_URL` | unset | Required when `AUTOCRAWLER_SOLVER=flaresolverr`. |
| `AUTOCRAWLER_CLEARANCE_TTL` | `30m` | Cached-clearance lifetime. |
| `AUTOCRAWLER_EGRESS_PROXY` | unset | Optional egress proxy for fetches. |
| `AUTOCRAWLER_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |

## Operator runbook

**Add a site.** Append an entry to `sites.yaml` (schema in
`deploy/sites.example.yaml`), then `autocrawl generate <slug>` → `autocrawl validate
<slug>` → `autocrawl crawl <slug>` to confirm before the daemon picks it up. The
daemon also generates missing crawlers for enabled sites on its own.

**Watch the fleet.** `autocrawl list` shows which sites have a committed script and
their last item count; `autocrawl status` shows run aggregates, the health
distribution (`healthy` / `stale` / `pending_review` / `generation_failed`), and the
observed drift-recovery MTTR.

**Promote a self-healed crawler.** When a site drifts, the daemon stages a
regenerated crawler. `status` shows it as `pending_review`; inspect
`<crawlers_dir>/_pending/<slug>.py`, then `autocrawl promote <slug>` to take it live.
(Set `AUTOCRAWLER_REVIEW_GATE=off` only if you want fully autonomous healing.)

**Improve the generator.** Run `autocrawl optimize` (offline; LM-heavy) to build the
trainset and emit a new `generators/generator-N.json`. Deploy it by pointing
`AUTOCRAWLER_GENERATOR_PATH` at it and restarting the daemon.

**Roll back a bad generator.** Flip `AUTOCRAWLER_GENERATOR_PATH` back to the previous
`generator-N.json` (or unset it to boot the baseline) and restart. Committed
crawlers are unaffected — only future generations change.

**Troubleshooting.**

- *`AUTOCRAWLER_MODEL is required` / `…SUB_MODEL is required`* — set both model slugs;
  there is intentionally no default.
- *Startup preflight failed* — the daemon fails fast (exit 2) on a missing/malformed
  `sites.yaml`, an invalid site entry, a bad `NEWS_API_URL`, or an unwritable state
  path. The log line names the cause.
- *Crawler exists but ingests nothing* — `autocrawl validate <slug>` to see item
  count + schema errors without ingesting; check the completeness floors
  (`MIN_BODY_CHARS`, `MIN_BODY_QUALITY`, `PAYWALL_MARKERS`).
- *Generated crawlers vanish after a container restart* — `AUTOCRAWLER_CRAWLERS_DIR`
  must point at a mounted volume, not the in-image package dir (see `deploy/`).
- *Cloudflare-challenged site* — set `AUTOCRAWLER_SOLVER=flaresolverr` +
  `FLARESOLVERR_URL`; the default `none` uses plain httpx.

## Development

Tooling and the TDD loop are pinned by the Nix flake:

```sh
nix develop -c pytest -q          # full suite (the RLM is mocked; no Deno, no network)
nix develop -c ty check           # type check (run before committing)
nix fmt                           # treefmt: nixfmt --strict + ruff-format
nix flake check                   # CI release gate: ty + the full pytest suite, hermetic
```

`nix build .#dockerImage` builds the deployable OCI image (see `deploy/`). dspy is
pinned to an exact version and guarded by a tripwire test
([`tests/test_dspy_pin.py`](tests/test_dspy_pin.py)): a bump fails CI until the
source-verified internals are re-checked (§9, [`DSPY_ASSUMPTIONS.md`](DSPY_ASSUMPTIONS.md)).

## Security notes

- The provider API key is read from the environment by litellm and never logged.
  Keep `autocrawler.env` at mode `600`; rotate keys out of band.
- The generator runs untrusted, model-written code only inside the §4.2 Deno/Pyodide
  sandbox (generate/optimize) and the §4.4 subprocess sandbox (validate/crawl) — never
  in the daemon process. Host-side thresholds are never exposed to it (§8).
