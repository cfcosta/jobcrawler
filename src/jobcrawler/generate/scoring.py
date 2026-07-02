"""The scoring core (§6 / §4.11): the graded multi-axis scalar.

Combine the quality axes by **shape-by-necessity**, not a flat product: a
``min``/AND **veto** for the axes where near-zero means worthless (on-domain-URL
validity, field presence) gates a **bounded weighted sum** of the graded axes
(items-yielded ratio, body-length rate, discovery-method quality). So
``item_count`` can never buy back an off-domain or empty-field crawler (veto →
0), while a single weak-but-acceptable graded axis cannot multiplicatively
crater an otherwise-good one (the failure a flat product invites).

Every axis is bounded to ``[0, 1]`` before it enters the scalar — the items
axis clamps at 1 (exceeding the target N buys nothing). The weights are seed
placeholders (precision body > recall items > process discovery), calibrated
against a downstream label per §14 Q8; the *combine form* is itself A/B'd vs a
flat product on the held-out.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from bizup_autocrawler.urls import normalize_host, valid_on_domain_url

if TYPE_CHECKING:  # heavy (litellm/tokenizers); only the metric needs it, lazily
    import dspy


class ExceptionInfo(BaseModel):
    """A candidate's exception, trimmed to its diagnostic tail (§4.2)."""

    type: str
    message: str
    traceback_tail: str


class CandidateResult(BaseModel):
    """The size-bounded result of running one candidate crawler (§5).

    Frozen — the §4.11 adapters, the gate_failures feedback transform, and the
    §8 adversarial admission all read these exact fields, never the model's
    self-reported counts.
    """

    model_config = ConfigDict(frozen=True)

    item_count: int = Field(ge=0)
    samples: list[dict] = Field(default_factory=list)
    schema_errors: list[str] = Field(default_factory=list)
    pages_fetched: int = Field(ge=0)
    exception: ExceptionInfo | None = None


# Discovery-method quality (feed > sitemap > scrape): a feed/sitemap enumerates,
# a homepage scrape only samples "recent".
DISCOVERY_QUALITY: dict[str, float] = {"feed": 1.0, "sitemap": 0.7, "scrape": 0.4}


class GradedWeights(BaseModel):
    items: float = Field(ge=0, le=1)
    body: float = Field(ge=0, le=1)
    discovery: float = Field(ge=0, le=1)


# Seed weights (placeholders; calibrate per §14 Q8). Sum to 1 so a perfect
# candidate scores exactly 1.0.
DEFAULT_WEIGHTS = GradedWeights(items=0.40, body=0.45, discovery=0.15)


class QualityAxes(BaseModel):
    """Per-candidate axis measurements, each bounded to [0, 1]."""

    items_ratio: float = Field(ge=0, le=1)
    field_presence: float = Field(ge=0, le=1)
    body_quality: float = Field(ge=0, le=1)
    url_validity: float = Field(ge=0, le=1)
    discovery_quality: float = Field(ge=0, le=1)


def items_axis(item_count: int, target_n: int) -> float:
    """``min(item_count / target_n, 1.0)`` — clamped; exceeding N buys nothing."""
    if target_n <= 0:
        raise ValueError("target_n must be positive")
    return min(item_count / target_n, 1.0)


def graded_score(
    axes: QualityAxes, *, weights: GradedWeights = DEFAULT_WEIGHTS
) -> float:
    """Combine the bounded axes by shape-by-necessity → a score in ``[0, 1]``."""
    veto = min(axes.url_validity, axes.field_presence)
    graded = (
        weights.items * axes.items_ratio
        + weights.body * axes.body_quality
        + weights.discovery * axes.discovery_quality
    )
    return veto * graded


def score_from_axes(
    axes: QualityAxes, *, forced: bool = False, weights: GradedWeights = DEFAULT_WEIGHTS
) -> float:
    """The graded scalar from pre-measured axes, minus the forced-extraction tie-breaker.

    Split out from :func:`score_candidate` so the GEPA/Evaluate metric can derive the
    score and the per-axis feedback from one ``compute_axes`` pass (the axes are the
    feedback signal GEPA reflects on; recomputing them twice would be wasteful).
    """
    score = graded_score(axes, weights=weights)
    if forced:
        score -= FORCED_EXTRACTION_PENALTY
    return max(0.0, score)


# A forced RLM extraction (hit max_iterations, §4.2) yields this sentinel as
# final_reasoning. It scores slightly below a deliberate SUBMIT — a tie-breaker
# (§6), never a veto: a forced extraction can still be a perfectly good crawler.
FORCED_FINAL_REASONING = "Extract forced final output"
FORCED_EXTRACTION_PENALTY = 0.05


def compute_axes(
    result: CandidateResult,
    *,
    target_n: int,
    allowed_hosts: Collection[str],
    min_body_chars: int,
    discovery_method: str,
    max_replacement_ratio: float = 0.02,
) -> QualityAxes:
    """Measure the five bounded [0,1] axes from a ``CandidateResult`` (§6).

    The veto axes (field presence, url validity) and the body axis are sampled
    rates over ``result.samples``; no samples means they are 0 (the veto fires).
    """
    samples = result.samples
    n = len(samples)
    if n == 0:
        field_presence = body_quality = url_validity = 0.0
    else:
        field_presence = sum(1 for s in samples if _has_fields(s)) / n
        body_quality = (
            sum(
                1
                for s in samples
                if _good_body(s, min_body_chars, max_replacement_ratio)
            )
            / n
        )
        url_validity = sum(1 for s in samples if _valid_url(s, allowed_hosts)) / n
    return QualityAxes(
        items_ratio=items_axis(result.item_count, target_n),
        field_presence=field_presence,
        body_quality=body_quality,
        url_validity=url_validity,
        discovery_quality=DISCOVERY_QUALITY.get(discovery_method, 0.0),
    )


def score_candidate(
    result: CandidateResult,
    *,
    target_n: int,
    allowed_hosts: Collection[str],
    min_body_chars: int,
    discovery_method: str,
    forced: bool = False,
    weights: GradedWeights = DEFAULT_WEIGHTS,
) -> float:
    """The graded scalar for one candidate, minus the forced-extraction tie-breaker."""
    axes = compute_axes(
        result,
        target_n=target_n,
        allowed_hosts=allowed_hosts,
        min_body_chars=min_body_chars,
        discovery_method=discovery_method,
    )
    return score_from_axes(axes, forced=forced, weights=weights)


def _has_fields(sample: Mapping[str, object]) -> bool:
    return all(str(sample.get(k) or "").strip() for k in ("url", "title", "body"))


def _good_body(
    sample: Mapping[str, object], min_body_chars: int, max_replacement_ratio: float
) -> bool:
    body = str(sample.get("body") or "").strip()
    if len(body) < min_body_chars:
        return False
    return body.count("�") / max(len(body), 1) <= max_replacement_ratio


def _valid_url(sample: Mapping[str, object], allowed_hosts: Collection[str]) -> bool:
    return valid_on_domain_url(str(sample.get("url") or ""), allowed_hosts)


# Per-axis remediation, surfaced when an axis is sub-maximal (§4.11). This text
# IS the optimization signal: GEPA's reflection LM never sees the numeric scores,
# only the feedback string (gepa 0.0.27 reflective_mutation). So a gate-passing
# but mediocre candidate must still get *actionable* prose — the empty-string the
# old transform returned gave the reflector nothing to mutate on. Each line is
# threshold-free (no ``<>=``), so the §8 scrub passes it through and the optimized
# loop learns *what to improve*, never the hidden bar. Phrased as "how the response
# could be better" (GEPA meta-prompt, Appendix 10).
_AXIS_LABEL: dict[str, str] = {
    "items_ratio": "item coverage",
    "field_presence": "field completeness",
    "body_quality": "body text",
    "url_validity": "url validity",
    "discovery_quality": "discovery method",
}
_AXIS_GUIDANCE: dict[str, str] = {
    "items_ratio": (
        "few articles enumerated — find a source that lists ALL recent articles "
        "(a content API, RSS/Atom feed, or sitemap) rather than scraping one page"
    ),
    "field_presence": (
        "some items are missing a title, body, or url — yield every item with all three"
    ),
    "body_quality": (
        "article bodies are short or garbled — extract the FULL article text and "
        "drop nav/boilerplate and encoding artefacts"
    ),
    "url_validity": (
        "some item urls are off-domain or malformed — yield only absolute, "
        "on-domain article urls"
    ),
    "discovery_quality": (
        "prefer a content API, RSS/Atom feed, or sitemap over scraping HTML pages"
    ),
}
# The order axes are considered for the breakdown when several tie on value: the
# veto axes first (a low veto caps the whole score), then the weighted axes.
_AXIS_ORDER = (
    "url_validity",
    "field_presence",
    "body_quality",
    "items_ratio",
    "discovery_quality",
)


def _axis_feedback(
    axes: QualityAxes, *, max_axes: int, epsilon: float = 1e-9
) -> list[str]:
    """Remediation lines for the worst sub-maximal axes (worst first), capped.

    Veto axes break value ties (``_AXIS_ORDER``) since a low veto caps the score.
    A maximal axis (``>= 1 - epsilon``) emits nothing — so a perfect candidate
    yields ``[]`` and ``build_feedback`` returns ``""`` (which GEPA harmlessly
    skips via ``skip_perfect_score``).
    """
    if max_axes <= 0:
        return []
    scored = [(name, getattr(axes, name)) for name in _AXIS_ORDER]
    sub_maximal = [(name, value) for name, value in scored if value < 1.0 - epsilon]
    sub_maximal.sort(key=lambda nv: (nv[1], _AXIS_ORDER.index(nv[0])))
    return [
        f"{_AXIS_LABEL[name]} low ({value:.2f}): {_AXIS_GUIDANCE[name]}"
        for name, value in sub_maximal[:max_axes]
    ]


# A forced extraction (hit max_iterations, §4.2) costs FORCED_EXTRACTION_PENALTY but
# touches no axis, so it can drive a candidate sub-perfect (0.95) while every axis is
# maximal → `_axis_feedback` is empty. Without a matching remedy line that candidate
# would hand GEPA `feedback=""` (the empty-feedback pitfall) for a non-perfect score
# GEPA does *not* skip. Threshold-free, so §8 holds.
_FORCED_REMEDY = (
    "extraction was force-completed at the iteration budget — make discovery converge "
    "sooner (commit to one discovery path early) so the crawler emits its final result "
    "deliberately instead of being cut off"
)


def build_feedback(
    *,
    gate_failures: Sequence[str],
    stability_note: str | None = None,
    max_foreground: int = 2,
    axes: QualityAxes | None = None,
    forced: bool = False,
) -> str:
    """The §4.11 feedback transform: concrete, severity-capped, across-run-aware.

    ``gate_failures`` arrive in pipeline-severity order (AST → completeness); we
    surface only the top ``max_foreground`` (many co-equal directives degrade
    compliance — later retries surface the next as gates clear). When ``axes`` are
    supplied and free foreground slots remain, the **worst sub-maximal axes** fill
    them — so a candidate that *passes* the gates but scores below 1.0 still gets
    actionable signal (without this, GEPA's reflection LM saw an empty string and
    had nothing to mutate). When ``forced`` and a slot still remains, a forced-
    extraction remedy fills it — the one sub-maximal cause an all-maximal-axis
    candidate has, so a 0.95 forced candidate never hands GEPA empty feedback. The
    stability verdict is appended as an across-run aggregate, never folded into the
    scalar. Numeric **thresholds are scrubbed** so the hidden-scorer rule (§8) holds
    — the optimized loop sees *what* to improve, never a target it could blank out.
    A pure transform over already-computed facts: no extra LLM-judge pass.
    """
    foreground = [_scrub_thresholds(line) for line in gate_failures[:max_foreground]]
    foreground = [line for line in foreground if line]
    if axes is not None and len(foreground) < max_foreground:
        foreground.extend(
            _axis_feedback(axes, max_axes=max_foreground - len(foreground))
        )
    if forced and len(foreground) < max_foreground:
        foreground.append(_FORCED_REMEDY)
    if stability_note:
        foreground.append(_scrub_thresholds(stability_note))
    return "; ".join(foreground)


# Strip scorer thresholds a candidate could target ("need >= 5", ">= 0.6") while
# keeping the candidate's own counts. §8: feedback names what failed, not the bar.
_THRESHOLD = re.compile(r",?\s*(?:need|require[ds]?)?\s*[<>]=?\s*\d+(?:\.\d+)?")


def _scrub_thresholds(text: str) -> str:
    return _THRESHOLD.sub("", text).strip()


# -- the three dspy adapters over one scoring core (§4.11) --------------------
#
# The core runs one candidate through validate_crawler against a replay-backed
# ctx and returns the CandidateResult. It is *injected* so the deterministic
# scoring/feedback wiring is testable without a live sandbox. Both adapters read
# the same host-computed facts — never the model's self-narrated counts (§8).

ScoringCore = Callable[[str, object], CandidateResult]


def make_reward_fn(
    core: ScoringCore,
    *,
    target_n: int,
    min_body_chars: int,
    weights: GradedWeights = DEFAULT_WEIGHTS,
) -> Callable[[Mapping[str, object], object], float]:
    """The BestOfN/Refine adapter (the outlier): ``reward_fn(kwargs, pred) -> float``."""

    def reward_fn(kwargs: Mapping[str, object], pred: object) -> float:
        result = core(_source_text(pred), kwargs)
        return score_candidate(
            result,
            target_n=target_n,
            allowed_hosts=_hosts(kwargs),
            min_body_chars=min_body_chars,
            discovery_method=_discovery(pred),
            forced=_forced(pred),
            weights=weights,
        )

    return reward_fn


def make_metric(
    core: ScoringCore,
    *,
    target_n: int,
    min_items: int,
    min_body_chars: int,
    weights: GradedWeights = DEFAULT_WEIGHTS,
) -> Callable[..., dspy.Prediction]:
    """The unified Evaluate/GEPA metric (signature-compatible — verified in 3.2.1).

    Returns ``Prediction(score, feedback)``: GEPA reads it as ScoreWithFeedback,
    Evaluate sums it via ``Prediction.__float__`` (reads ``.score``). Gates run
    cheapest-first (AST short-circuits before the core ever runs the candidate);
    feedback names *what* failed, never the threshold (§8).
    """

    def metric(
        gold: object,
        pred: object,
        trace: object = None,
        pred_name: str | None = None,
        pred_trace: object = None,
    ) -> dspy.Prediction:
        import dspy  # lazy: heavy import, and only the metric constructs Prediction
        from bizup_autocrawler.generate import gates  # lazy: break the import cycle

        source = _source_text(pred)
        ast_result = gates.check_ast(source)
        if not ast_result.passed:
            return dspy.Prediction(
                score=0.0, feedback=build_feedback(gate_failures=ast_result.failures)
            )

        hosts = _hosts(gold)
        result = core(source, gold)
        completeness = gates.check_completeness(
            result,
            min_items=min_items,
            min_body_chars=min_body_chars,
            allowed_hosts=hosts,
        )
        # One axis pass feeds both the scalar and the per-axis remediation, so a
        # gate-passing-but-mediocre candidate still hands GEPA actionable feedback
        # (the empty-string path gave the reflection LM nothing — see build_feedback).
        forced = _forced(pred)
        axes = compute_axes(
            result,
            target_n=target_n,
            allowed_hosts=hosts,
            min_body_chars=min_body_chars,
            discovery_method=_discovery(pred),
        )
        score = score_from_axes(axes, forced=forced, weights=weights)
        return dspy.Prediction(
            score=score,
            feedback=build_feedback(
                gate_failures=completeness.failures, axes=axes, forced=forced
            ),
        )

    return metric


def make_bootstrap_metric(
    core: ScoringCore,
    *,
    target_n: int,
    min_body_chars: int,
    weights: GradedWeights = DEFAULT_WEIGHTS,
) -> Callable[..., float]:
    """The BootstrapFewShot adapter (§4.11 / M5a): a bare **float** score.

    DSPy's ``BootstrapFewShot`` gates a self-generated demo on ``metric_threshold``,
    which compares the metric's *float* return — unlike ``make_metric``'s
    ``Prediction`` (for GEPA/Evaluate). Same scoring core; the AST gate short-
    circuits to 0.0 before the core runs (cheapest-first, §9). The trainset is
    reference-free, so the metric ignores any gold and scores the produced crawler.
    """

    def metric(
        gold: object, pred: object, trace: object = None, *_extra: object
    ) -> float:
        from bizup_autocrawler.generate import gates  # lazy: break the import cycle

        source = _source_text(pred)
        if not gates.check_ast(source).passed:
            return 0.0
        result = core(source, gold)
        return score_candidate(
            result,
            target_n=target_n,
            allowed_hosts=_hosts(gold),
            min_body_chars=min_body_chars,
            discovery_method=_discovery(pred),
            forced=_forced(pred),
            weights=weights,
        )

    return metric


def _field(carrier: object, name: str, default: object = None) -> object:
    if isinstance(carrier, Mapping):
        return carrier.get(name, default)
    return getattr(carrier, name, default)


def _source_text(pred: object) -> str:
    src = _field(pred, "crawler_source", "")
    code = getattr(src, "code", None)  # dspy.Code carries the string on .code
    return code if isinstance(code, str) else str(src)


def _discovery(pred: object) -> str:
    return str(_field(pred, "discovery_method", "scrape"))


def _forced(pred: object) -> bool:
    return _field(pred, "final_reasoning", "") == FORCED_FINAL_REASONING


def _hosts(carrier: object) -> set[str]:
    return {normalize_host(str(_field(carrier, "site_url", "")))}
