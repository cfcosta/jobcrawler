"""Project persisted match verdicts into a DSPy trainset for the matcher (§8).

The §4.7 matcher runs a large model. To later optimise a *smaller* model to reproduce its
judgments (distillation), or to correct them against the operator's own accept/reject
signal, we need a labelled dataset in the exact shape :class:`~jobcrawler.match.MatchJobs`
consumes. Every scored ``positions`` row already carries that data durably — the job
fields, the full verdict (score / band / reasons / concerns), and the operator's
``track_status`` / ``dismissed`` feedback, all on one row — so this module *projects* rows
into :class:`dspy.Example` rather than adding a write path to the hot match loop.

The ``profile`` input is the current ``PROFILE.md`` body: a profile edit rescores every
open row (§16), so the live verdicts stay consistent with the current profile and a single
snapshot faithfully labels them. Feedback correction is the whole point of persisting the
operator signal — a row they engaged with (interested / applied / interviewing / offer /
accepted) is relabelled a strong match; one they dismissed or rejected / withdrew is
relabelled a reject. The operator overrules the model.

The ``jobs`` digest and band thresholds are reused verbatim from :mod:`jobcrawler.match`
(``_digest`` / ``_verdict_label``) so a trainset example is byte-identical to what the live
matcher would have sent — training on a drifted input distribution would defeat the point.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

import dspy

from jobcrawler.match import _digest, _verdict_label, candidate_from_row
from jobcrawler.state import PositionRecord

MATCH_INPUT_FIELDS = ("profile", "jobs")
_OUTPUT_FIELD = "verdicts"

# track_status values that reveal the operator's own fit judgment, overruling the LLM.
_ENGAGED = frozenset({"interested", "applied", "interviewing", "offer", "accepted"})
_REJECTED = frozenset({"rejected", "withdrawn"})


def _json_list(raw: str | None) -> list[str]:
    """Decode a persisted reasons/concerns JSON array, tolerating null/garbage as ``[]``."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def corrected_label(rec: PositionRecord) -> dict[str, object] | None:
    """The feedback-corrected verdict for one scored row, or ``None`` if it has no score.

    Starts from the persisted LLM verdict, then lets the operator's own signal overrule the
    score/band: dismissed / rejected / withdrawn becomes a hard ``reject``; an engaged
    ``track_status`` becomes a hard ``strong``. Reasons/concerns ride along as the rationale
    trace the smaller model can be taught to reproduce.
    """
    if rec.match_score is None:
        return None
    reasons = _json_list(rec.match_reasons)
    concerns = _json_list(rec.match_concerns)
    score = max(0, min(100, int(rec.match_score)))
    band = rec.match_verdict or _verdict_label(score)
    if rec.dismissed or rec.track_status in _REJECTED:
        score, band = 10, "reject"
        concerns = concerns or ["operator dismissed or rejected this posting"]
    elif rec.track_status in _ENGAGED:
        score, band = max(score, 85), "strong"
        reasons = reasons or ["operator engaged with this posting"]
    return {
        "index": 0,
        "score": score,
        "verdict": band,
        "reasons": reasons,
        "concerns": concerns,
    }


def example_from_position(
    rec: PositionRecord, profile_body: str, *, label: dict[str, object]
) -> dspy.Example | None:
    """One ``dspy.Example(profile, jobs -> verdicts)`` for a scored row.

    Returns ``None`` when the row can't be faithfully re-presented host-side (missing
    description or content-hash drift — the same guard :func:`candidate_from_row` applies on
    the drain path), so the ``jobs`` input is exactly what the live matcher would have sent.
    """
    enriched = candidate_from_row(rec)
    if enriched is None:
        return None
    jobs = json.dumps([_digest(0, enriched)], ensure_ascii=False)
    verdicts = json.dumps([label], ensure_ascii=False)
    return dspy.Example(
        profile=profile_body, jobs=jobs, verdicts=verdicts
    ).with_inputs(*MATCH_INPUT_FIELDS)


def build_examples(
    rows: Iterable[PositionRecord], profile_body: str
) -> list[dspy.Example]:
    """Project scored ``positions`` rows into matcher trainset examples.

    Skips rows with no verdict and rows that can't be faithfully rebuilt (so the trainset
    only ever contains inputs identical to production ones).
    """
    out: list[dspy.Example] = []
    for rec in rows:
        label = corrected_label(rec)
        if label is None:
            continue
        example = example_from_position(rec, profile_body, label=label)
        if example is not None:
            out.append(example)
    return out


def save_trainset(examples: Sequence[dspy.Example], path: str) -> None:
    """Write examples as JSONL — one ``{profile, jobs, verdicts}`` object per line."""
    fields = (*MATCH_INPUT_FIELDS, _OUTPUT_FIELD)
    lines = [
        json.dumps({f: example.get(f) for f in fields}, ensure_ascii=False)
        for example in examples
    ]
    Path(path).write_text(("\n".join(lines) + "\n") if lines else "")


def load_trainset(path: str) -> list[dspy.Example]:
    """Read a JSONL matcher trainset back into inputs-tagged ``dspy.Example``s."""
    out: list[dspy.Example] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(dspy.Example(**json.loads(line)).with_inputs(*MATCH_INPUT_FIELDS))
    return out
