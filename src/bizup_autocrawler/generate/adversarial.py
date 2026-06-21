"""Adversarial admission (§8): cross-validate a candidate's claimed items.

The reward (item-count + field-presence + stability) is a gameable proxy — a
crawler could pad/fabricate items or scrape off-domain. Before a pair is admitted
as a positive exemplar (§4.11) or a generator is promoted, its items are
cross-validated against **independent** signals: URLs resolve on-domain, titles
match the live ``<title>``, and bodies are not boilerplate duplicates. Acceptance
rests on the **actual** items, never the model's claimed counts (the "imagined
variable" risk). The live-``<title>`` fetch is the caller's; this is the pure
adjudicator over what was fetched.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from bizup_autocrawler.urls import canonicalize_url, valid_on_domain_url


@dataclass(frozen=True, slots=True)
class AdversarialResult:
    passed: bool
    off_domain: int
    title_mismatches: int
    duplicate_bodies: int
    reason: str = ""


def cross_validate(
    samples: Sequence[Mapping[str, object]],
    *,
    live_titles: Mapping[str, str],
    allowed_hosts: Collection[str],
    min_title_match: float = 0.5,
    max_duplicate_ratio: float = 0.5,
) -> AdversarialResult:
    """Cross-validate ``samples`` against independent signals (§8)."""
    if not samples:
        return AdversarialResult(False, 0, 0, 0, "no items to cross-validate")

    n = len(samples)
    off_domain = sum(
        1
        for s in samples
        if not valid_on_domain_url(str(s.get("url") or ""), allowed_hosts)
    )
    title_mismatches = sum(1 for s in samples if not _title_matches(s, live_titles))
    duplicate_bodies = _duplicate_count(samples)

    failures: list[str] = []
    if off_domain:
        failures.append(f"{off_domain} off-domain urls")
    if (n - title_mismatches) / n < min_title_match:
        failures.append("titles do not match the live <title>")
    if duplicate_bodies / n > max_duplicate_ratio:
        failures.append("bodies are boilerplate duplicates")
    return AdversarialResult(
        not failures,
        off_domain,
        title_mismatches,
        duplicate_bodies,
        "; ".join(failures),
    )


def _title_matches(
    sample: Mapping[str, object], live_titles: Mapping[str, str]
) -> bool:
    raw = str(sample.get("url") or "")
    live = live_titles.get(canonicalize_url(raw)) or live_titles.get(raw)
    if live is None:
        return True  # no live title fetched for this url → best-effort, don't penalize
    title = str(sample.get("title") or "").strip().lower()
    live_lower = live.strip().lower()
    return bool(title) and (title in live_lower or live_lower in title)


def _duplicate_count(samples: Sequence[Mapping[str, object]]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for sample in samples:
        body = str(sample.get("body") or "").strip()
        if body and body in seen:
            duplicates += 1
        seen.add(body)
    return duplicates
