"""The site registry (§4.1): ``sites.yaml`` → ``list[Site]``.

A checked-in list is the system's input — one entry per board, validated into the
:class:`~jobcrawler.schedule.Site` model (re-exported here) that the scheduler,
generator, and runner consume. ``jobcrawl generate`` writes a crawler per slug;
``jobcrawl run`` schedules them. The registry columns:

  * ``slug`` — stable id; names ``crawlers/<slug>.py`` and the ``source`` stamped
    on every :class:`~jobcrawler.runtime.contract.Job`, so it must be a valid
    Python identifier (underscores, never hyphens).
  * ``url`` — the endpoint/page the crawler starts from. It also fixes the §8
    SSRF host allowlist (fetches must stay on this host), so it points straight at
    the ATS API host where there is one (e.g. ``boards-api.greenhouse.io``).
  * ``name`` — human label for ``list`` / ``status``.
  * ``company`` (job-domain, §4.1) — the company/org the board belongs to, for
    operator-facing display/notify. A single-company board (a Greenhouse/Lever/
    Ashby/Gupy token whose ATS feed carries no per-posting employer) sets it, and
    it doubles as the generator's steer to stamp that company onto each Job; a
    multi-company aggregator (RemoteOK, We Work Remotely) omits it and each
    posting names its own employer. Optional, ``None`` by default.
  * ``enumerates_all`` (job-domain, §4.1) — whether one crawl sees the *whole*
    board (``True``, the default, so the §4.7 close-missing diff may soundly close
    any URL absent from a healthy crawl) or only a recent *window* (``False`` for
    an RSS feed or RemoteOK's latest-~100, where the diff would false-close roles
    that merely scrolled off — closure there defers to the §4.9 recheck loop).
  * ``render`` / ``hints`` / ``schedule`` / ``adaptive`` / ``enabled`` — fetch
    tier, generator steer, cadence, and on-off (see :class:`Site`).

The :class:`Site` model itself — every field, validator, and the full ``company``
/ ``enumerates_all`` rationale — lives in :mod:`jobcrawler.schedule`; this module
is the loader over it, so ``from jobcrawler.sites import Site`` and
``from jobcrawler.schedule import Site`` are the same class.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml

from jobcrawler import config
from jobcrawler.schedule import Site

__all__ = ["Site", "find_site", "load_sites"]


def load_sites(path: str, *, adaptive_default: bool | None = None) -> list[Site]:
    """Parse ``sites.yaml`` into validated Site entries (empty file → no sites).

    A site that pins neither ``schedule`` nor ``adaptive`` inherits the fleet default
    cadence mode (§4.9 ``JOBCRAWLER_ADAPTIVE_DEFAULT``): adaptive when that is set,
    else the fixed ``default_schedule``. An explicit ``schedule:`` or ``adaptive:`` on
    the entry always wins. ``adaptive_default`` overrides the config read (for tests).
    """
    default_adaptive = (
        config.adaptive_default() if adaptive_default is None else adaptive_default
    )
    raw = yaml.safe_load(Path(path).read_text()) or []
    sites = [Site.model_validate(entry) for entry in raw]
    if default_adaptive:
        for site in sites:
            if not _mode_pinned(site):
                site.adaptive = True
    return sites


def _mode_pinned(site: Site) -> bool:
    """True if the registry entry set its cadence mode explicitly — a fixed/adaptive
    ``schedule:`` or an ``adaptive:`` flag — so the fleet default must not override it.
    (``schedule: adaptive`` is normalized to the ``adaptive`` flag by the model, but
    still counts as set via ``model_fields_set``.)"""
    return "schedule" in site.model_fields_set or "adaptive" in site.model_fields_set


def find_site(sites: Sequence[Site], slug: str) -> Site | None:
    return next((s for s in sites if s.slug == slug), None)
