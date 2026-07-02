"""The §4.11 trainset — accepted (snapshot → crawler) examples, inputs-only.

One ``dspy.Example`` per accepted generation, mirroring the four GenerateCrawler
inputs with ``.with_inputs`` (mandatory). It is **reference-free**: the metric
scores by *running a fresh crawler*, so the recorded ``discovery_*`` /
``crawler_source`` are provenance, not labels. A pair is admitted iff §6 gates
1–3 pass, the §8 adversarial cross-validation passes, and the site stayed
non-stale for ``AUTOCRAWLER_TRAINSET_STABLE_RUNS`` runs (more conservative than
the §4.8 single-run regen trigger — a stale site yields a misleading reward and
pollutes GEPA's frontier). The frozen ``corpus`` is persisted per example as
plain JSON so generation replays reproducibly. The trainset is its own logical
namespace; train / val / promotion are disjoint pools (asserted here — overlap
is a leak that GEPA would exploit).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import dspy

INPUT_FIELDS = ("site_url", "corpus", "render", "hints")
_PROVENANCE = ("discovery_method", "discovery_url", "crawler_source")
_ALL_FIELDS = INPUT_FIELDS + _PROVENANCE


def make_example(
    *,
    site_url: str,
    corpus: dict[str, object],
    render: str,
    hints: str,
    discovery_method: str,
    discovery_url: str,
    crawler_source: str,
) -> dspy.Example:
    """Build an inputs-only trainset example (mirrors GenerateCrawler)."""
    return dspy.Example(
        site_url=site_url,
        corpus=corpus,
        render=render,
        hints=hints,
        discovery_method=discovery_method,
        discovery_url=discovery_url,
        crawler_source=crawler_source,
    ).with_inputs(*INPUT_FIELDS)


def admit(
    *,
    gates_passed: bool,
    adversarial_passed: bool,
    stable_runs: int,
    required_stable: int,
) -> bool:
    """Admit a pair iff gates 1–3 + §8 cross-val pass and it stayed non-stale."""
    return gates_passed and adversarial_passed and stable_runs >= required_stable


def assert_disjoint(*pools: Sequence[dspy.Example]) -> None:
    """Raise if any two pools share a site (a train/val/promotion leak, §4.11)."""
    seen: set[str] = set()
    for pool in pools:
        sites = {ex.site_url for ex in pool}
        overlap = seen & sites
        if overlap:
            raise ValueError(
                f"pool overlap (train/val/promotion leak): {sorted(overlap)}"
            )
        seen |= sites


def save_trainset(examples: Sequence[dspy.Example], path: str) -> None:
    rows = [
        {field: _jsonable(ex.get(field), field) for field in _ALL_FIELDS}
        for ex in examples
    ]
    Path(path).write_text(json.dumps(rows))


def load_trainset(path: str) -> list[dspy.Example]:
    rows = json.loads(Path(path).read_text())
    return [make_example(**row) for row in rows]


def _jsonable(value: object, field: str) -> object:
    # corpus stays a dict; everything else is coerced to str (crawler_source may
    # arrive as a dspy.Code).
    return value if field == "corpus" else str(value)


# -- accumulation: record on accept, admit on sustained stability (§4.11) -----


def append_record(path: str, *, slug: str, example: dspy.Example) -> None:
    """Append one **accepted** generation to the JSONL records store (§4.11).

    Written only after the §6 gates and the §8 adversarial cross-validation pass;
    the per-site *stability* admission depends on later runs, so it is applied at
    optimize time (:func:`optimization_pool`), not here. JSONL so concurrent-safe
    appends never read-modify-write the whole file.
    """
    row: dict[str, object] = {
        "slug": slug,
        "kind": "accepted",
        **{field: _jsonable(example.get(field), field) for field in _ALL_FIELDS},
    }
    _append(path, row)


def record_failure(
    path: str, *, slug: str, site_url: str, render: str = "auto", hints: str = ""
) -> None:
    """Record a confirmed real failure (a drifted / generation-failed site) as a
    **hard case** for the optimizer (§4.11).

    Hard cases are the discriminative, sub-maximal signal GEPA learns from — the
    ``SATURATION_REMEDY``'s "recently-drifted / barely-passing" bias made concrete.
    Only the *site identity* is stored; the corpus is re-prefetched fresh at optimize
    time, so the strategy is scored against the site's CURRENT (drifted) state rather
    than a stale snapshot. The caller must already have confirmed the failure is real
    (the §4.8 re-probe), not a transient outage.
    """
    _append(
        path,
        {
            "slug": slug,
            "kind": "hard",
            "site_url": site_url,
            "render": render,
            "hints": hints,
        },
    )


def _append(path: str, row: dict[str, object]) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def load_records(path: str) -> list[dict[str, object]]:
    """Read the JSONL records store (``[]`` if absent)."""
    file = Path(path)
    if not file.exists():
        return []
    return [
        json.loads(line)
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def admitted_examples(
    records: Sequence[Mapping[str, object]],
    *,
    stable_runs: Callable[[str], int],
    required_stable: int,
) -> list[dspy.Example]:
    """The admitted trainset: records whose site stayed non-stale long enough (§4.11).

    Gates + adversarial were enforced at record time (only passes are appended), so
    admission here is the per-site stability condition the spec layers on top — more
    conservative than the §4.8 single-run regen trigger, since a stale site yields a
    misleading reward and pollutes the optimizer's frontier.
    """
    out: list[dspy.Example] = []
    for row in records:
        slug = str(row.get("slug", ""))
        if admit(
            gates_passed=True,
            adversarial_passed=True,
            stable_runs=stable_runs(slug),
            required_stable=required_stable,
        ):
            out.append(_example_from_row(row))
    return out


def _example_from_row(row: Mapping[str, object]) -> dspy.Example:
    """Rebuild a trainset example from a persisted record (typed-safe coercion)."""
    raw = row.get("corpus")
    corpus: dict[str, object] = (
        {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}
    )
    return make_example(
        site_url=str(row.get("site_url", "")),
        corpus=corpus,
        render=str(row.get("render", "")),
        hints=str(row.get("hints", "")),
        discovery_method=str(row.get("discovery_method", "")),
        discovery_url=str(row.get("discovery_url", "")),
        crawler_source=str(row.get("crawler_source", "")),
    )


def optimization_pool(
    records: Sequence[Mapping[str, object]],
    *,
    stable_runs: Callable[[str], int],
    required_stable: int,
    prefetch: Callable[[str], dict[str, object]],
) -> list[dspy.Example]:
    """The optimizer's trainset: stable accepted exemplars **plus** hard failures.

    Accepted records are admitted once their site clears the stability bar and keep
    their frozen corpus; hard cases (confirmed failures) are always included and have
    their corpus **re-prefetched** via ``prefetch`` so the strategy is scored on the
    site's current (drifted) state. The failures supply the sub-maximal spread that a
    success-only pool lacks — without them the optimizer saturates and no-ops (§4.11).
    """
    out: list[dspy.Example] = []
    for row in records:
        if row.get("kind") == "hard":
            site_url = str(row.get("site_url", ""))
            out.append(
                make_example(
                    site_url=site_url,
                    corpus=prefetch(site_url),
                    render=str(row.get("render", "auto") or "auto"),
                    hints=str(row.get("hints", "")),
                    discovery_method="",
                    discovery_url="",
                    crawler_source="",
                )
            )
        elif admit(
            gates_passed=True,
            adversarial_passed=True,
            stable_runs=stable_runs(str(row.get("slug", ""))),
            required_stable=required_stable,
        ):
            out.append(_example_from_row(row))
    return out


def split_by_site(
    examples: Sequence[dspy.Example], *, holdout: int = 1, val: int = 1
) -> tuple[list[dspy.Example], list[dspy.Example], list[dspy.Example]]:
    """Partition examples into disjoint ``(train, val, holdout)`` by **site** (§4.11).

    A whole site goes to exactly one pool — a site appearing in two pools is the
    train/val/promotion leak :func:`assert_disjoint` rejects. Sites are ordered
    deterministically; the last ``holdout`` sites are the frozen promotion pool the
    optimizer never sees, the next ``val`` the validation pool, the rest train.
    """
    by_site: dict[str, list[dspy.Example]] = {}
    for example in examples:
        by_site.setdefault(str(example.site_url), []).append(example)
    sites = sorted(by_site)

    hold_sites = sites[len(sites) - holdout :] if holdout > 0 else []
    remaining = sites[: len(sites) - holdout] if holdout > 0 else sites
    val_sites = remaining[len(remaining) - val :] if val > 0 else []
    train_sites = remaining[: len(remaining) - val] if val > 0 else remaining

    def _flatten(chosen: list[str]) -> list[dspy.Example]:
        return [example for site in chosen for example in by_site[site]]

    return _flatten(train_sites), _flatten(val_sites), _flatten(hold_sites)
