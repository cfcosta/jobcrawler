"""The personal application-tracking axis (§16): where a posting sits in *your* pipeline.

This is the operator-owned status a job moves through as you pursue it —
``new -> interested -> applied -> interviewing -> offer`` and the terminal outcomes
``accepted``/``rejected``/``withdrawn`` — plus a ``dismissed`` hide flag, a free-text
``notes`` field, and an ``applied_at`` date. It is **orthogonal** to the crawler-owned
open/closed lifecycle in §4.7: a posting can be board-``closed`` while you are still
``interviewing``, and dismissing a row in the UI never touches its crawl ``status``.

Per the §4.7 "the store records state, never invents policy" rule (and the §8 hidden-
scorer boundary), ``state.py`` persists ``track_status`` as an opaque string and never
adjudicates it. The *valid set*, the display order, the labels/colours, and which stages
imply an application was actually submitted are host-side policy and live here — read by
the web UI (`jobcrawler.web`) and used by its callers to decide when to stamp
``applied_at``. Transitions are unconstrained (a personal search moves backwards and skips
stages freely); only the *value* written is validated, via `parse`.
"""

from __future__ import annotations

from enum import StrEnum


class TrackStatus(StrEnum):
    """The fixed set of personal pipeline stages. `StrEnum`, so a member *is* its stored string."""

    NEW = "new"
    INTERESTED = "interested"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


#: The status a freshly-crawled posting carries until you touch it. Mirrors the
#: ``track_status TEXT NOT NULL DEFAULT 'new'`` column default in ``state.py``'s schema.
DEFAULT = TrackStatus.NEW

#: Display/selection order: the active funnel first (``new`` -> ``offer``), then the
#: terminal outcomes. Drives the status dropdown and the dashboard's stage columns.
ORDER: tuple[TrackStatus, ...] = (
    TrackStatus.NEW,
    TrackStatus.INTERESTED,
    TrackStatus.APPLIED,
    TrackStatus.INTERVIEWING,
    TrackStatus.OFFER,
    TrackStatus.ACCEPTED,
    TrackStatus.REJECTED,
    TrackStatus.WITHDRAWN,
)

#: The active funnel (open pursuits worth chasing) — everything that is not terminal.
PIPELINE: frozenset[TrackStatus] = frozenset(
    {
        TrackStatus.NEW,
        TrackStatus.INTERESTED,
        TrackStatus.APPLIED,
        TrackStatus.INTERVIEWING,
        TrackStatus.OFFER,
    }
)

#: Terminal outcomes: no further action is expected once a posting lands here.
TERMINAL: frozenset[TrackStatus] = frozenset(
    {TrackStatus.ACCEPTED, TrackStatus.REJECTED, TrackStatus.WITHDRAWN}
)

#: Stages that imply an application was actually submitted. Entering any of these for the
#: first time is when a caller stamps ``applied_at`` (see `implies_applied`). ``rejected``
#: and ``withdrawn`` are deliberately excluded — you can be rejected or withdraw *before*
#: ever applying; and if you had applied, the stamp is already set and is never cleared.
APPLIED_IMPLYING: frozenset[TrackStatus] = frozenset(
    {
        TrackStatus.APPLIED,
        TrackStatus.INTERVIEWING,
        TrackStatus.OFFER,
        TrackStatus.ACCEPTED,
    }
)

#: Human labels for the badges/dropdown.
LABELS: dict[TrackStatus, str] = {
    TrackStatus.NEW: "New",
    TrackStatus.INTERESTED: "Interested",
    TrackStatus.APPLIED: "Applied",
    TrackStatus.INTERVIEWING: "Interviewing",
    TrackStatus.OFFER: "Offer",
    TrackStatus.ACCEPTED: "Accepted",
    TrackStatus.REJECTED: "Rejected",
    TrackStatus.WITHDRAWN: "Withdrawn",
}

#: A CSS-class token per status (styled in ``web/static/app.css``). Kept here so the
#: palette is host-side policy, not scattered through the templates.
CSS_TOKENS: dict[TrackStatus, str] = {
    TrackStatus.NEW: "neutral",
    TrackStatus.INTERESTED: "info",
    TrackStatus.APPLIED: "progress",
    TrackStatus.INTERVIEWING: "warn",
    TrackStatus.OFFER: "good",
    TrackStatus.ACCEPTED: "success",
    TrackStatus.REJECTED: "danger",
    TrackStatus.WITHDRAWN: "muted",
}

_VALUES: frozenset[str] = frozenset(s.value for s in TrackStatus)


def parse(value: str) -> TrackStatus:
    """Coerce an operator-supplied string to a `TrackStatus`; raise `ValueError` if unknown.

    The host-side validation seam (§8): the web UI calls this before writing, so the store
    only ever persists a value from the fixed set. Any *transition* is allowed; only the
    value is constrained.
    """
    try:
        return TrackStatus(value)
    except ValueError:
        raise ValueError(
            f"unknown track status {value!r}; expected one of "
            + ", ".join(s.value for s in ORDER)
        ) from None


def is_valid(value: str) -> bool:
    """True if ``value`` is a known status string (no exception; for lenient callers)."""
    return value in _VALUES


def implies_applied(status: TrackStatus | str) -> bool:
    """True if entering ``status`` means an application was submitted (stamp ``applied_at``)."""
    return (
        TrackStatus(status) if isinstance(status, str) else status
    ) in APPLIED_IMPLYING


def is_terminal(status: TrackStatus | str) -> bool:
    """True if ``status`` is a terminal outcome (no further pipeline action)."""
    return (TrackStatus(status) if isinstance(status, str) else status) in TERMINAL


def promotable_to_interested(current: TrackStatus | str) -> bool:
    """True only when ``current`` is ``new`` — the one transition the quick-triage
    ``i`` keybinding is allowed to make.

    This is a deliberately narrow, one-way guard (§16): the keyboard shortcut exists
    to flag a *fresh* posting as worth a look, not to resurrect a ``rejected`` or
    ``withdrawn`` row back into the active funnel. Every other transition still goes
    through the full status dropdown, which stays unconstrained. Note this is host-side
    *keybinding* policy, not a store invariant — `parse` still accepts any value.
    """
    return (
        TrackStatus(current) if isinstance(current, str) else current
    ) == TrackStatus.NEW


def label(value: str) -> str:
    """Human label for a stored status string; falls back to Title Case for legacy values."""
    try:
        return LABELS[TrackStatus(value)]
    except ValueError:
        return value.replace("_", " ").title()


def css_token(value: str) -> str:
    """CSS-class token for a stored status string; ``neutral`` for anything unknown."""
    try:
        return CSS_TOKENS[TrackStatus(value)]
    except ValueError:
        return "neutral"


def choices() -> list[tuple[str, str, str]]:
    """``(value, label, css_token)`` in display order — for the UI dropdown and badges."""
    return [(s.value, LABELS[s], CSS_TOKENS[s]) for s in ORDER]
