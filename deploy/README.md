# Deploying bizup-autocrawler

How to run the self-scheduling crawl daemon (`autocrawl run`, §4.9) as a managed
service — either as a container or natively. For what the system *is*, the CLI
surface, and the full configuration reference, see the project `README.md` at the
repo root.

Files in this directory:

| File | Purpose |
| --- | --- |
| `autocrawler.env.example` | The env file both unit files read — copy, fill the four required values, `chmod 600`. |
| `sites.example.yaml` | A minimal, schema-correct site registry to copy into your state dir. |
| `bizup-autocrawler.service` | systemd unit running the OCI image via podman/docker (**recommended**). |
| `bizup-autocrawler-native.service` | systemd unit running the Nix-built console script directly (no container runtime). |

## What you must provide

Four values are mandatory; everything else defaults sanely (see root `README.md`).

| Variable | Why it's required |
| --- | --- |
| `OPENROUTER_API_KEY` | The LM provider key. Read by litellm via the model slug's `openrouter/` prefix — swap for `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` if you point the slugs elsewhere. Needed by `generate`/`optimize`; **not** by `run`/`crawl`. |
| `AUTOCRAWLER_MODEL` | RLM root planner — a strong, code-capable model. No default (an unset value fails loud at generation rather than 404-ing deep in the provider). |
| `AUTOCRAWLER_SUB_MODEL` | RLM `sub_lm` — a cheaper code model for in-sandbox `llm_query`. Required so in-sandbox calls don't silently bill at the root planner's rate. |
| `NEWS_API_URL` | Where committed crawlers POST articles (the jobcrawler ingest API). |

> **Container networking gotcha:** `NEWS_API_URL`'s default `http://localhost:8000`
> resolves to *the container itself*. Point it at the host or the news service —
> `host.containers.internal` (podman), `host.docker.internal` (docker, add
> `--add-host=host.docker.internal:host-gateway`), a LAN IP, or a shared-network
> service name.

## Option A — container (recommended)

The image is built by Nix, so it can never drift from `uv.lock` the way a
hand-rolled `pip install` Dockerfile would. It bundles the Python runtime closure,
the `autocrawl` console script, and Deno (for the §4.2 generation sandbox).

```sh
# 1. Build the loadable layered image (no Docker daemon needed to build).
nix build .#packages.x86_64-linux.dockerImage     # → ./result (a .tar.gz)

# 2. Load it into your runtime (podman shown; `docker` is identical).
podman load < result                              # → bizup-autocrawler:latest

# 3. Quick smoke test.
podman run --rm bizup-autocrawler:latest autocrawl --help
```

The image bakes the `/data` contract, so the only host wiring is a bind-mount plus
the env file:

| Baked env | Value |
| --- | --- |
| `AUTOCRAWLER_SITES` | `/data/sites.yaml` |
| `AUTOCRAWLER_STATE_URL` | `sqlite:////data/autocrawler.db` |
| `AUTOCRAWLER_CRAWLERS_DIR` | `/data/crawlers` (committed crawlers + `_pending` staging — kept on the volume so they survive restarts; the in-image package dir is ephemeral) |
| `DENO_DIR` | `/data/deno-cache` (Pyodide WASM cache — persisted so generation cold-starts don't re-download) |
| `Cmd` | `autocrawl run` |
| `WorkingDir` | `/data` |

Run it ad-hoc:

```sh
podman run --rm --name bizup-autocrawler \
  --env-file /etc/bizup-autocrawler/autocrawler.env \
  --volume /var/lib/bizup-autocrawler:/data \
  bizup-autocrawler:latest
```

For a managed service, install `bizup-autocrawler.service` (its header has the full
copy-paste install sequence) and `systemctl enable --now bizup-autocrawler`.

### Generate crawlers first

`run`/`crawl` execute committed per-site crawlers; a fresh deployment has none.
Generate them once (this is the only step that needs the LM + Deno):

```sh
podman run --rm --env-file /etc/bizup-autocrawler/autocrawler.env \
  --volume /var/lib/bizup-autocrawler:/data \
  bizup-autocrawler:latest autocrawl generate --all
```

Generated crawlers land in `AUTOCRAWLER_CRAWLERS_DIR` — baked to `/data/crawlers`,
i.e. on the mounted volume — so they persist across container restarts and `--rm`
runs; no image rebuild is needed to add or update a crawler. (This is why the dir is
relocated off the in-image package dir, which is ephemeral in the container layer —
and read-only on a native Nix deploy.) The §4.8 self-healer stages regenerated
crawlers to `/data/crawlers/_pending/<slug>.py`; review and `autocrawl promote
<slug>` to take one live.

## Option B — native (no container runtime)

Run straight from the Nix-built console script. The crawl daemon never invokes
Deno, so a native `run` host needs **no Deno on PATH** — only interactive
`generate`/`optimize` do.

```sh
nix build .#default --out-link /var/lib/bizup-autocrawler/app   # → app/bin/autocrawl
```

Install `bizup-autocrawler-native.service` (its header has the full sequence,
including the system user and absolute state paths). It adds systemd hardening
(`ProtectSystem=strict`, a single `ReadWritePaths`, outbound-only address families)
since it runs directly on the host.

## Healthcheck story

The image declares an OCI `Healthcheck` that runs **`autocrawl status`** every 60s
(10s timeout, 5s start period, 3 retries). `status` loads the site registry and
opens the state store, then prints per-site run aggregates, the fleet health
distribution, and the observed drift-recovery MTTR (§4.8/M4). So the healthcheck is
a **readiness/config probe** — it catches a missing or malformed `sites.yaml` and an
unreadable state DB — *not* a daemon liveness heartbeat (a true liveness surface
awaits the spec's future HTTP endpoint).

- **podman/docker:** the healthcheck is honored automatically; see it in
  `podman ps` (STATUS column) or force a run with
  `podman healthcheck run bizup-autocrawler`.
- **systemd:** units don't read the OCI healthcheck directly. Use it as an external
  probe or a watchdog wrapper:

  ```sh
  podman run --rm --env-file /etc/bizup-autocrawler/autocrawler.env \
    --volume /var/lib/bizup-autocrawler:/data \
    bizup-autocrawler:latest autocrawl status
  ```

  Native deploys run the same `…/app/bin/autocrawl status` as a probe.

## Day-one checklist

1. `nix build` the image (or `.#default`) and load/link it.
2. `install -d -m 700 /var/lib/bizup-autocrawler /etc/bizup-autocrawler`.
3. Copy `autocrawler.env.example` → `/etc/bizup-autocrawler/autocrawler.env`,
   fill the four required values, `chmod 600`.
4. Copy `sites.example.yaml` → `/var/lib/bizup-autocrawler/sites.yaml`, edit to
   your real seed list.
5. `autocrawl generate --all` once to produce the per-site crawlers.
6. Install the chosen `.service` file, `daemon-reload`, `enable --now`.
7. Verify: `autocrawl status` shows your sites; `journalctl -u bizup-autocrawler -f`
   shows the scheduler ticking.
