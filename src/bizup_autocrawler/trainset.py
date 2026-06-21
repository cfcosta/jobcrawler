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
from collections.abc import Sequence
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
