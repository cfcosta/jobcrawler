"""The candidate profile: prefilter preferences + LLM matching context (§8 host-side).

``PROFILE.md`` (repo root, committed) is the single source of *what the operator
wants*. It has two halves, deliberately split so the cheap deterministic layer and
the expensive LLM layer each read the part they need:

  * **YAML frontmatter** (between ``---`` fences) — machine-readable preferences the
    §6 deterministic ``prefilter`` in :mod:`jobcrawler.match` reads to drop definite
    mismatches *before* any token is spent. Every key is optional; an empty/omitted
    key means "no constraint" (recall-safe — the LLM still judges it).
  * **free markdown body** — the rich narrative (skills, domains, what to reject)
    handed verbatim to the host-side matcher LM as context. Prose the prefilter can't
    encode lives here.

The whole file is content-addressed by :attr:`Profile.profile_hash` (sha256 of the
normalized raw text). That hash is one of the four keys of the match cache
(content, profile, model, prompt-version), so **editing the profile changes the hash
and lazily invalidates every cached verdict** — new/changed/pending jobs re-score
against the new profile, already-scored jobs stay put until they're re-examined. This
is why the hash is over the *raw* text, not just the prefs: a body-only edit (new
context for the LLM) must also trigger rescoring.

Enum-typed prefs (``disciplines``/``levels``/``workplace``) are validated against the
same classification vocabulary the host-side normalizer emits (§4.6), so a preference
can never reference a bucket a job can't be classified into — a typo fails loud at
load time (naming the bad value) rather than silently matching nothing. This module is
host-side only (§8): it is read outside the crawl sandbox and never exposed to the
generator/RLM.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

# Classification vocabularies — MUST mirror the enums :mod:`jobcrawler.runtime.normalize`
# emits (§4.6). Duplicated here on purpose: profile loading is a host-side boot step
# that must validate prefs even while the normalizer module is unavailable, and the
# values are pinned by spec, so a local copy is the single validation source without a
# hard import coupling. If the normalizer's buckets change, change these in lockstep.
DISCIPLINES: frozenset[str] = frozenset(
    {
        "backend",
        "frontend",
        "fullstack",
        "mobile",
        "devops_sre",
        "data",
        "ml_ai",
        "security",
        "qa",
        "embedded",
        "eng_other",
        "non_eng",
    }
)
LEVELS: frozenset[str] = frozenset(
    {
        "intern",
        "junior",
        "mid",
        "senior",
        "staff_plus",
        "tech_lead",
        "manager",
        "director_plus",
        "unspecified",
    }
)
WORKPLACES: frozenset[str] = frozenset({"remote", "hybrid", "onsite", "unspecified"})

# Salary periods a floor may be expressed in — the same set the §4.6 salary parser
# normalizes to. A per-week/day floor is not supported (jobs don't post those).
_PERIODS: frozenset[str] = frozenset({"year", "month", "hour"})


class ProfileNotFound(FileNotFoundError):
    """Raised when ``PROFILE.md`` is absent, carrying a create-it hint.

    A subclass of ``FileNotFoundError`` so a caller that only wants "no profile" can
    still catch the stdlib type, while the daemon/CLI can catch this specifically to
    print the actionable "create PROFILE.md" message instead of a bare traceback.
    """


@dataclass(frozen=True, slots=True)
class SalaryFloor:
    """A minimum-compensation preference: amount in a currency over a period.

    Kept structured (not free text) because the prefilter/matcher compare it against
    a job's *parsed* :class:`~jobcrawler.runtime.normalize.SalaryRange`; comparing a
    number to a number needs no NL parsing. ``period`` is one of year/month/hour.
    """

    amount: float
    currency: str
    period: str


@dataclass(frozen=True, slots=True)
class ProfilePrefs:
    """The machine-readable prefilter preferences (the YAML frontmatter, parsed).

    Every field is "no constraint when empty": an empty ``frozenset``/``tuple`` (or
    ``None`` floor) means the prefilter imposes nothing on that axis and defers to the
    LLM (recall-safe). Enum-typed sets hold only values from the §4.6 vocabularies;
    the free-text tuples (locations/languages/exclusions) are lower-cased *at compare
    time* by the prefilter, not here, so the original casing survives for display.
    """

    disciplines: frozenset[str]
    levels: frozenset[str]
    workplace: frozenset[str]
    locations: tuple[str, ...]
    min_salary: SalaryFloor | None
    languages: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    exclude_companies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Profile:
    """The loaded profile: raw text + parsed body + prefs + content hash.

    ``body`` (the markdown after the frontmatter) is the LLM matching context;
    ``prefs`` drives the deterministic prefilter; ``profile_hash`` keys the match
    cache so a profile edit lazily invalidates prior verdicts (see module docstring).
    """

    raw: str
    body: str
    prefs: ProfilePrefs
    profile_hash: str


def load_profile(path: str) -> Profile:
    """Read and validate ``PROFILE.md`` at ``path`` into a :class:`Profile`.

    Raises :class:`ProfileNotFound` (with a create-it hint) when the file is missing,
    and ``ValueError`` naming the offending value when a prefs enum is unknown or a
    field is malformed — validation is loud at load time so a bad profile is caught on
    boot, not by silently matching nothing. YAML is parsed with ``yaml.safe_load``
    (never ``load``): the frontmatter is operator-authored config, not code.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileNotFound(
            f"no profile found at {path!r}: create a PROFILE.md with YAML frontmatter "
            "(disciplines/levels/workplace/... preferences) followed by a free-text "
            "markdown body describing your skills and what to reject"
        ) from exc

    frontmatter, body = _split_frontmatter(raw)
    prefs = _parse_prefs(frontmatter)
    profile_hash = hashlib.sha256(_normalize_raw(raw).encode("utf-8")).hexdigest()
    return Profile(raw=raw, body=body.strip(), prefs=prefs, profile_hash=profile_hash)


# --- frontmatter splitting -------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[str, str]:
    """Split ``raw`` into (yaml_frontmatter, markdown_body).

    A file that does not open with a ``---`` fence has no frontmatter — it is treated
    as an all-body profile (no prefs, LLM-only), which is a legitimate minimal profile.
    An opened-but-unclosed fence is a mistake we surface loudly rather than guess at.
    """
    text = raw.lstrip("﻿")  # tolerate a UTF-8 BOM
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    raise ValueError(
        "PROFILE.md opens a '---' frontmatter fence that is never closed; add a "
        "matching '---' line before the markdown body"
    )


def _normalize_raw(raw: str) -> str:
    """Canonicalize text before hashing so cosmetic churn doesn't force a rescore.

    Line endings are unified and trailing per-line whitespace stripped: a CRLF/LF flip
    or a stray trailing space is not a semantic profile change and must not bust the
    match cache. Anything that actually alters the operator's intent (a pref, a body
    sentence) still changes the hash.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


# --- prefs parsing / validation --------------------------------------------


def _parse_prefs(frontmatter: str) -> ProfilePrefs:
    data = yaml.safe_load(frontmatter) if frontmatter.strip() else None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(
            f"PROFILE.md frontmatter must be a YAML mapping, got {type(data).__name__}"
        )
    return ProfilePrefs(
        disciplines=_enum_set(data.get("disciplines"), DISCIPLINES, "disciplines"),
        levels=_enum_set(data.get("levels"), LEVELS, "levels"),
        workplace=_enum_set(data.get("workplace"), WORKPLACES, "workplace"),
        locations=_str_tuple(data.get("locations"), "locations"),
        min_salary=_parse_salary_floor(data.get("min_salary")),
        languages=_str_tuple(data.get("languages"), "languages"),
        exclude_keywords=_str_tuple(data.get("exclude_keywords"), "exclude_keywords"),
        exclude_companies=_str_tuple(
            data.get("exclude_companies"), "exclude_companies"
        ),
    )


def _as_list(value: object, field: str) -> list[object]:
    """Coerce a scalar-or-list YAML value to a list (``None`` -> empty).

    A single value (``workplace: remote``) is accepted as a one-element list so the
    operator isn't forced to write a YAML sequence for a lone preference.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(
        f"{field} must be a string or a list of strings, got {type(value).__name__}"
    )


def _enum_set(value: object, valid: frozenset[str], field: str) -> frozenset[str]:
    """Validate an enum-typed pref list against ``valid`` (§4.6 vocabulary).

    Values are lower-cased before the membership check (so ``Backend`` matches
    ``backend``); an unknown value raises ``ValueError`` naming it and listing the
    accepted set, so a typo can't quietly become a filter that matches nothing.
    """
    out: set[str] = set()
    for item in _as_list(value, field):
        if not isinstance(item, str):
            raise ValueError(
                f"unknown {field} value: {item!r} (must be one of {sorted(valid)})"
            )
        norm = item.strip().lower()
        if norm not in valid:
            raise ValueError(
                f"unknown {field} value: {item!r} (must be one of {sorted(valid)})"
            )
        out.add(norm)
    return frozenset(out)


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    """Coerce a free-text pref list to a tuple of non-empty stripped strings."""
    out: list[str] = []
    for item in _as_list(value, field):
        text = str(item).strip()
        if text:
            out.append(text)
    return tuple(out)


def _parse_salary_floor(value: object) -> SalaryFloor | None:
    """Parse the structured ``min_salary`` mapping into a :class:`SalaryFloor`.

    A floor is structured YAML (``{amount, currency, period}``), not free text, so the
    comparison against a job's parsed salary needs no NL parsing (§4.6 owns the fuzzy
    text→number parsing; a preference is authored, so it's exact). ``period`` defaults
    to ``year`` (the usual quoted unit for the senior roles this targets).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            "min_salary must be a mapping with amount/currency/period, got "
            f"{type(value).__name__}"
        )
    if "amount" not in value:
        raise ValueError("min_salary requires an 'amount'")
    raw_amount = value.get("amount")
    if not isinstance(raw_amount, (int, float, str)):
        raise ValueError(f"min_salary.amount must be a number, got {raw_amount!r}")
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        raise ValueError(
            f"min_salary.amount must be a number, got {raw_amount!r}"
        ) from None
    currency = str(value.get("currency", "")).strip().upper()
    if not currency:
        raise ValueError("min_salary requires a 'currency' (e.g. USD, BRL, EUR)")
    period = str(value.get("period", "year")).strip().lower()
    if period not in _PERIODS:
        raise ValueError(
            f"min_salary.period must be one of {sorted(_PERIODS)}, got {period!r}"
        )
    return SalaryFloor(amount=amount, currency=currency, period=period)
