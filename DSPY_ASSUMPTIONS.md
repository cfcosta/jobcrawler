# DSPy assumptions — source-pinned (no smoke test)

Every load-bearing DSPy-internal claim in `SPEC.md` is recorded here against the **actual source**, with
`file:line` and a one-line note on what the code does. This replaces a runtime smoke test: the spec is
kept honest by **static citation + a version tripwire**, not by importing dspy at build time.

| | |
|---|---|
| **dspy version** | `3.2.1` (pinned dep of `bizup-news`) |
| **Source of truth** | the dspy package — cloned repo (`+ tests/`) and the byte-identical wheel at `news/.venv/.../site-packages/dspy/` |
| **NOT a source of truth** | ⚠️ `news/.claude/skills/dspy-rlm/SKILL.md` — it invents `SandboxSerializable` / `to_sandbox` / `rlm_preview`, which **do not exist** in 3.2.1 (grep returns 0 in repo *and* wheel). Verify against source, never the skill. |
| **On a dspy bump** | re-run the source audit (workflow `wfh296hq1`) and update this file. A CI check asserts the installed dspy version still equals the version above and **fails** (prompting a re-audit) on mismatch — no import, no Deno. |

Paths below are relative to the dspy package root (`dspy/`).

## RLM (`predict/rlm.py`, `primitives/python_interpreter.py`, `primitives/repl_types.py`)

| Claim | Verified behavior | `file:line` |
|---|---|---|
| Root loop is **bounded** (not "unbounded") | `for iteration in range(self.max_iterations)`; `max_iterations` default **20**; `_extract_fallback` on exhaustion (`final_reasoning="Extract forced final output"`) | `rlm.py:128, 597, 606, 435/625` |
| `max_llm_calls` is **soft/recoverable** | default **50**; in-process counter raises `RuntimeError` once `call_count+n > max_llm_calls` (catchable in-sandbox) | `rlm.py:231-238` |
| RLM **exposes** optimizer-targetable predictors | `self.generate_action`, `self.extract` are `dspy.Predict` (enumerable via `named_predictors()`) | `rlm.py:163-164` |
| RLM has `aforward` | async path exists | `rlm.py:660` |
| `corpus` injection is **JSON-literal**, no pickle/SandboxSerializable | `_inject_variables` → `_serialize_value` emits a Python literal; allowed types `{None,str,bool,int,float,list,tuple,dict,set}`; **sets → sorted lists**; **raises `CodeInterpreterError("Unsupported value type")`** only for types *outside* that set (e.g. `complex`, `bytes`, arbitrary object) | `python_interpreter.py:419, 469-478` |
| Large vars spill to a temp file | `> LARGE_VAR_THRESHOLD` (100 MB) → `/tmp/dspy_vars/{k}.json`, `json.loads` in-sandbox | `python_interpreter.py:28, 437` |
| REPL output truncation is **10k total** | `half = max_output_chars // 2` (= 5000) → `head[:5k] + "(N omitted)" + tail[-5k:]`; default `max_output_chars=10_000` | `repl_types.py:112-119`, `rlm.py:130` |
| `serialize_for_json` **never raises** (do not test against it) | falls back to `str(value)` | `adapters/utils.py:42` |

## Selection — BestOfN / Refine (`predict/best_of_n.py`, `predict/refine.py`)

| Claim | Verified behavior | `file:line` |
|---|---|---|
| Both are **sequential** (not concurrent) | `def forward` only (no `aforward`/`asyncio`/`gather`) — a for-loop over rollouts with an early break | `best_of_n.py:55`, `refine.py:98` |
| Candidate **distinctness = `rollout_id` + temperature**, not `cache=False` | each attempt `lm.copy(rollout_id=rid, temperature=1.0)`; `rollout_id` **is** in the cache key (`ignored_args=['api_key','api_base','base_url']`), so distinct attempts re-reason even with cache **on** | `best_of_n.py:62`, `refine.py:107`, `cache.py:112-113, 233`, `lm.py:148`; test `tests/clients/test_lm.py:134-178` |
| At temperature 0, `rollout_id` is inert | distinctness needs temperature > 0 | `lm.py:139-145` |
| Stock `Refine` does **not** carry our `gate_failures` | hint = `inspect.getsource(reward_fn)` + scalar via an internal `OfferFeedback` `dspy.Predict` | `refine.py:92-96, 167` |
| `getsource` breakage is `functools.partial`-only | `getsource(reward_fn)` guarded by `except TypeError`; a **lambda works**; a `partial` makes the fallback `getsource(partial.__class__)` raise an *uncaught* `OSError` | `refine.py:92-96` |
| `reward_fn` is the one signature outlier | `reward = self.reward_fn(kwargs, pred)` — 1st arg is the module **kwargs dict**, returns a bare float | `best_of_n.py:72` |

## Evaluate / GEPA / save (`evaluate/evaluate.py`, `teleprompt/gepa/`, `primitives/base_module.py`)

| Claim | Verified behavior | `file:line` |
|---|---|---|
| `dspy.Evaluate` calls the metric with **2 args** | `score = metric(example, prediction)` — **no** `trace`; `trace` is supplied only by optimizers | `evaluate.py:172`; `bootstrap_trace.py:57` |
| Evaluate + GEPA metrics are **compatible** (one callable) | GEPA's 5-arg metric `(gold, pred, trace=None, pred_name=None, pred_trace=None)` is reused via `Evaluate`/bootstrap | `gepa.py:551`, `tests/teleprompt/test_gepa.py:38` |
| GEPA takes **separate** `trainset`/`valset` (no internal split) | `compile(student, *, trainset, teacher=None, valset=None)`; `valset = valset or trainset` (reuses trainset if omitted — **not** guaranteed disjoint) | `gepa.py:465-471, 508` |
| `D_feedback` / `D_pareto` are paper terms only | grep over dspy + gepa pkg returns **0** | — |
| `reflection_lm` is a real GEPA kwarg | `reflection_lm: LM | None = None`; reflects over a *trace-derived* dataset | `gepa.py:341`, `gepa_utils.py:214` |
| `sub_lm` is **RLM-only** (not GEPA) | grep `sub_lm` over dspy → only `rlm.py` | `rlm.py:133` |
| GEPA selector options | `candidate_selection_strategy: Literal["pareto","current_best"]` (default `pareto`); greedy keeps hill-climbing but loses frontier diversity; engine's `epsilon_greedy` is **not** exposed by `dspy.GEPA` | `gepa.py:340`, `engine.py:493`, `candidate_selector.py:31-33` |
| `save(save_program=False)` dispatches on **suffix** | `.json` → JSON state (`orjson`); `.pkl` → **cloudpickle even with `save_program=False`**; other → `ValueError`. Always use a `.json` path. | `base_module.py:168, 229-249` |

## bizup-news integration (`news/src/bizup_news/`)

| Claim | Verified behavior | `file:line` |
|---|---|---|
| `POST /news` accepts `{title, body}` | `CreateNewsRequest(title, body)` | `news/router.py` |
| Each ingest drives **2–3 server-side LLM calls** | `add_news` → `summarize.summarize` + `classify.classify` (classify retries once on invalid id) | `news/service.py:18-19`, `news/classify.py` |
| **No** server-side idempotency | bare `INSERT`, no `url` column / `ON CONFLICT` (idempotency exists only in `likes`/`tags`) | `news/repository.py` |

## Forward-looking (does **not** exist yet)

`CrawlerGenerator`, the `crawler_source`/`corpus`/`discovery_*` signature fields, and every `AUTOCRAWLER_*`
env var are **intended design**, not present code — `grep` over `news/src` returns nothing, and the only
live RLM call site (`news/.../reports/summarize.py:37`) passes neither `max_iterations` nor `max_llm_calls`
(relies on the 20/50 defaults). The spec describes these as design; verify them against *this file* once built.
