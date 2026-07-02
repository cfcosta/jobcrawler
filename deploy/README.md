# Deploying jobcrawler

How to run the self-scheduling job-tracker daemon (`jobcrawl run`, §4.9) as a
managed service — either as a container or natively. The daemon crawls job boards,
diffs open positions against the state ledger, and scores each new posting against
your `PROFILE.md` with a cheap host-side match LM (§4.6). For what the system *is*,
the CLI surface, and the full configuration reference, see the project `README.md`
at the repo root.

Files in this directory:

| File | Purpose |
| --- | --- |
| `jobcrawler.env.example` | The env file both unit files read — copy, fill the three required values, `chmod 600`. It also enumerates the full override surface. |
| `sites.example.yaml` | A minimal, schema-correct site registry (with ATS `company`/`enumerates_all` examples) to copy into your state dir. |
| `jobcrawler.service` | systemd unit running the OCI image via podman/docker (**recommended**). |
| `jobcrawler-native.service` | systemd unit running the Nix-built console script directly (no container runtime). |

## What you must provide

Three env values **and** a `PROFILE.md` are mandatory; everything else defaults
sanely (see the full annotated surface in `jobcrawler.env.example` and the prose in
the root `README.md`).

| Variable | Why it's required |
| --- | --- |
| `OPENROUTER_API_KEY` | The LM provider key. Read by litellm via the model slug's `openrouter/` prefix — swap for `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` if you point the slugs elsewhere. Needed by `generate`/`optimize` **and by `run`**: the §4.6 matcher scores new positions with an LM (the committed crawl loop itself still calls no LM). |
| `JOBCRAWLER_MODEL` | RLM root planner — a strong, code-capable model. No default (an unset value fails loud at generation rather than 404-ing deep in the provider). Used only by `generate`/`optimize`. |
| `JOBCRAWLER_SUB_MODEL` | RLM `sub_lm` — a cheaper code model for in-sandbox `llm_query`, **and** the §4.6 matcher's fallback model. Required so in-sandbox calls (and matching) don't silently bill at the root planner's rate. On a `run`-only host this is the one model slug you must set. |

> **`PROFILE.md` is required input, not an env var.** The matcher has nothing to
> score against without it, so the daemon says so loudly rather than matching blind.
> It lives on the persistent volume beside `sites.yaml` (`JOBCRAWLER_PROFILE`,
> default `/data/PROFILE.md` in the container). See [The match profile](#the-match-profile-profilemd).

## The match profile (`PROFILE.md`)

`PROFILE.md` is the single source of *what you want*. It has two halves, split so
the cheap deterministic layer and the expensive LLM layer each read the part they
need:

- **YAML frontmatter** (between `---` fences) — machine-readable preferences the
  deterministic prefilter reads to drop definite mismatches *before spending a
  token*: `disciplines`, `levels`, `workplace`, `locations`, `min_salary`
  (`{amount, currency, period}`), `languages`, `exclude_keywords`,
  `exclude_companies`. Every key is optional (omit ⇒ no constraint on that axis).
  The enum keys are validated against the classifier vocabulary — a typo like
  `disciplines: [backend, freontend]` fails loud at boot naming the bad value,
  rather than silently matching nothing.
- **free markdown body** — your narrative (skills, domains, what to reject), handed
  verbatim to the match LM as context. Prose the prefilter can't encode lives here.

Copy the repo-root `PROFILE.md` as a starting template and rewrite it for yourself.
The whole file is content-addressed: **editing it lazily invalidates cached
verdicts**, so new/changed/pending jobs re-score against the new profile while
already-scored jobs stay put until they're next examined. A body-only edit (new LLM
context) rescopes matches too — the hash is over the whole file.

### Matching cost knobs (§4.6)

The matcher is the only new token cost a `run` host carries. It is bounded on
several axes (all in `jobcrawler.env.example`); the defaults are conservative:

| Knob | Default | What it bounds |
| --- | --- | --- |
| `JOBCRAWLER_MATCH_MODEL` | *(falls back to `JOBCRAWLER_SUB_MODEL`)* | Which model scores — never the expensive root planner. |
| `JOBCRAWLER_MATCH_DAILY_CAP` | `400` | Hard ceiling on **LLM-scored** jobs per UTC day. Cache hits are free; jobs past the cap wait as `match_pending` (never dropped). |
| `JOBCRAWLER_MATCH_BATCH` | `10` | Jobs per LLM call — amortizes the profile+rubric prompt prefix. |
| `JOBCRAWLER_MATCH_RATE` | `10/m` | Paces the match calls so a big sweep can't burst the provider. |
| `JOBCRAWLER_MATCH_MIN_SCORE` | `70` | Score at/above which a match fires a notification (not a drop — everything is scored and stored). |
| `JOBCRAWLER_FIRST_CRAWL_CAP` | `30` | Newest N jobs matched on a site's first crawl; the standing backlog waits, so a fresh board can't blow the daily cap. |

A verdict is cached by (job content, profile, model, prompt version), so steady-state
matching is nearly free — only genuinely new or changed postings cost tokens.

### Position lifecycle & the recheck loop (§4.7 / §4.9)

The daemon tracks each posting from open to closed:

- On an **enumerating** board (`enumerates_all: true`, the default — the crawler
  sees the *whole* board each pass), a role that disappears for
  `JOBCRAWLER_CLOSE_AFTER_MISSES` (default `2`) consecutive successful crawls is
  marked closed. A single miss is treated as a transient hiccup; closure only
  advances after a **successful, non-empty** crawl, so a failed fetch can never
  mass-close a board.
- On a **non-enumerating** source (`enumerates_all: false` — an RSS or latest-N
  feed that only ever shows a recent window), the diff is unsound, so closure comes
  solely from the **recheck loop**: every `JOBCRAWLER_RECHECK_INTERVAL` (default
  `12h`) it re-fetches up to `JOBCRAWLER_RECHECK_CAP` (default `150`) standing open
  positions, oldest-checked first, and closes any that 404 or carry an
  expired-marker. It also re-verifies enumerating rows against `expires_at` /
  `JOBCRAWLER_EXPIRED_MARKERS`.

## Option A — container (recommended)

The image is built by Nix, so it can never drift from `uv.lock` the way a
hand-rolled `pip install` Dockerfile would. It bundles the Python runtime closure,
the `jobcrawl` console script, and Deno (for the §4.2 generation sandbox).

```sh
# 1. Build the loadable layered image (no Docker daemon needed to build).
nix build .#packages.x86_64-linux.dockerImage     # → ./result (a .tar.gz)

# 2. Load it into your runtime (podman shown; `docker` is identical).
podman load < result                              # → jobcrawler:latest

# 3. Quick smoke test.
podman run --rm jobcrawler:latest jobcrawl --help
```

The image bakes the `/data` contract, so the only host wiring is a bind-mount plus
the env file:

| Baked env | Value |
| --- | --- |
| `JOBCRAWLER_SITES` | `/data/sites.yaml` |
| `JOBCRAWLER_PROFILE` | `/data/PROFILE.md` (the §4.6 match profile — an operator-authored persistent input like `sites.yaml`) |
| `JOBCRAWLER_STATE_URL` | `sqlite:////data/jobcrawler.db` (run history **plus** the positions ledger and the match cache/spend) |
| `JOBCRAWLER_CRAWLERS_DIR` | `/data/crawlers` (committed crawlers + `_pending` staging — kept on the volume so they survive restarts; the in-image package dir is ephemeral) |
| `DENO_DIR` | `/data/deno-cache` (Pyodide WASM cache — persisted so generation cold-starts don't re-download) |
| `Cmd` | `jobcrawl run` |
| `WorkingDir` | `/data` |

Run it ad-hoc:

```sh
podman run --rm --name jobcrawler \
  --env-file /etc/jobcrawler/jobcrawler.env \
  --volume /var/lib/jobcrawler:/data \
  jobcrawler:latest
```

For a managed service, install `jobcrawler.service` (its header has the full
copy-paste install sequence) and `systemctl enable --now jobcrawler`.

### Generate crawlers first

`run`/`crawl` execute committed per-site crawlers; a fresh deployment has none.
Generate them once (this is the only step that needs Deno — the §4.2 Pyodide
sandbox; it also needs the LM key, as does `run` for the §4.6 matcher):

```sh
podman run --rm --env-file /etc/jobcrawler/jobcrawler.env \
  --volume /var/lib/jobcrawler:/data \
  jobcrawler:latest jobcrawl generate --all
```

Generated crawlers land in `JOBCRAWLER_CRAWLERS_DIR` — baked to `/data/crawlers`,
i.e. on the mounted volume — so they persist across container restarts and `--rm`
runs; no image rebuild is needed to add or update a crawler. (This is why the dir is
relocated off the in-image package dir, which is ephemeral in the container layer —
and read-only on a native Nix deploy.) The §4.8 self-healer stages regenerated
crawlers to `/data/crawlers/_pending/<slug>.py`; review and `jobcrawl promote
<slug>` to take one live.

## Option B — native (no container runtime)

Run straight from the Nix-built console script. The crawl daemon never invokes
Deno, so a native `run` host needs **no Deno on PATH** — only interactive
`generate`/`optimize` do.

```sh
nix build .#default --out-link /var/lib/jobcrawler/app   # → app/bin/jobcrawl
```

Install `jobcrawler-native.service` (its header has the full sequence,
including the system user and absolute state paths). It adds systemd hardening
(`ProtectSystem=strict`, a single `ReadWritePaths`, outbound-only address families)
since it runs directly on the host.

## Healthcheck story

The image declares an OCI `Healthcheck` that runs **`jobcrawl status`** every 60s
(10s timeout, 5s start period, 3 retries). `status` loads the site registry and
opens the state store, then prints per-site run aggregates, the fleet health
distribution, and the observed drift-recovery MTTR (§4.8/M4). So the healthcheck is
a **readiness/config probe** — it catches a missing or malformed `sites.yaml` and an
unreadable state DB — *not* a daemon liveness heartbeat (a true liveness surface
awaits the spec's future HTTP endpoint).

- **podman/docker:** the healthcheck is honored automatically; see it in
  `podman ps` (STATUS column) or force a run with
  `podman healthcheck run jobcrawler`.
- **systemd:** units don't read the OCI healthcheck directly. Use it as an external
  probe or a watchdog wrapper:

  ```sh
  podman run --rm --env-file /etc/jobcrawler/jobcrawler.env \
    --volume /var/lib/jobcrawler:/data \
    jobcrawler:latest jobcrawl status
  ```

  Native deploys run the same `…/app/bin/jobcrawl status` as a probe.

## Day-one checklist

1. `nix build` the image (or `.#default`) and load/link it.
2. `install -d -m 700 /var/lib/jobcrawler /etc/jobcrawler`.
3. Copy `jobcrawler.env.example` → `/etc/jobcrawler/jobcrawler.env`,
   fill the three required values, `chmod 600`.
4. Copy `sites.example.yaml` → `/var/lib/jobcrawler/sites.yaml`, edit to
   your real seed list.
5. Copy the repo-root `PROFILE.md` → `/var/lib/jobcrawler/PROFILE.md` and
   rewrite it for yourself (frontmatter prefs + narrative body, §4.6).
6. `jobcrawl generate --all` once to produce the per-site crawlers.
7. Install the chosen `.service` file, `daemon-reload`, `enable --now`.
8. Verify: `jobcrawl status` shows your sites; `journalctl -u jobcrawler -f`
   shows the scheduler ticking and matches landing.
