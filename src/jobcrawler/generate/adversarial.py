"""Adversarial admission (§8): cross-validate a candidate's claimed positions.

The reward (position-count + field-presence + stability) is a gameable proxy — a
crawler could pad/fabricate postings or scrape off-domain. Before a pair is
admitted as a positive exemplar (§4.11) or a generator is promoted, its positions
are cross-validated against **independent** signals: URLs resolve on-domain,
titles match the live posting-page ``<title>``, and descriptions are not the same
page yielded for every row. Acceptance rests on the **actual** positions, never
the model's claimed counts (the "imagined variable" risk). The live-``<title>``
fetch is the caller's; this is the pure adjudicator over what was fetched.

**Verbatim-duplicate descriptions, not shared boilerplate.** A company board
legitimately repeats About/EEO/benefits blocks across every posting — that is why
the §6 completeness gate *strips* shared blocks and grades the unique residual,
rather than rejecting on shared text. This adversarial check is the coarser,
complementary probe: it flags only *byte-identical whole descriptions* across
postings, the signature of a crawler that yielded the board index (or an About
page) as each "posting" instead of the per-role text — a fabrication two distinct
roles never trip, however much boilerplate they share.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from jobcrawler.urls import canonicalize_url, valid_on_domain_url


@dataclass(frozen=True, slots=True)
class AdversarialResult:
    passed: bool
    off_domain: int
    title_mismatches: int
    duplicate_descriptions: int
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
        return AdversarialResult(False, 0, 0, 0, "no positions to cross-validate")

    n = len(samples)
    off_domain = sum(
        1
        for s in samples
        if not valid_on_domain_url(str(s.get("url") or ""), allowed_hosts)
    )
    title_mismatches = sum(1 for s in samples if not _title_matches(s, live_titles))
    duplicate_descriptions = _duplicate_count(samples)

    failures: list[str] = []
    if off_domain:
        failures.append(f"{off_domain} off-domain urls")
    if (n - title_mismatches) / n < min_title_match:
        failures.append("titles do not match the live posting-page <title>")
    if duplicate_descriptions / n > max_duplicate_ratio:
        failures.append("postings share one verbatim description (index page yielded?)")
    return AdversarialResult(
        not failures,
        off_domain,
        title_mismatches,
        duplicate_descriptions,
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
    """Count postings whose *whole* description repeats one already seen (§8).

    Exact-match on the full description string — not a shared-block check (the §6
    gate owns that). Byte-identical descriptions across distinct postings mean the
    crawler yielded one shared page (the board index / an About page) as each row.
    """
    seen: set[str] = set()
    duplicates = 0
    for sample in samples:
        description = str(sample.get("description") or "").strip()
        if description and description in seen:
            duplicates += 1
        seen.add(description)
    return duplicates
