"""The site registry (§4.1): ``sites.yaml`` → ``list[Site]``.

A checked-in list is the system's input — one entry per site, validated into the
:class:`~bizup_autocrawler.schedule.Site` model the scheduler and CLI consume.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import yaml

from bizup_autocrawler import config
from bizup_autocrawler.schedule import Site


def load_sites(path: str, *, adaptive_default: bool | None = None) -> list[Site]:
    """Parse ``sites.yaml`` into validated Site entries (empty file → no sites).

    A site that pins neither ``schedule`` nor ``adaptive`` inherits the fleet default
    cadence mode (§4.9 ``AUTOCRAWLER_ADAPTIVE_DEFAULT``): adaptive when that is set,
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
