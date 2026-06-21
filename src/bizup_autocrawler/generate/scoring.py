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

from pydantic import BaseModel, Field

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
