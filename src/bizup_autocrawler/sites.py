"""The site registry (§4.1): ``sites.yaml`` → ``list[Site]``.

A checked-in list is the system's input — one entry per site, validated into the
:class:`~bizup_autocrawler.schedule.Site` model the scheduler and CLI consume.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bizup_autocrawler.schedule import Site


def load_sites(path: str) -> list[Site]:
    """Parse ``sites.yaml`` into validated Site entries (empty file → no sites)."""
    raw = yaml.safe_load(Path(path).read_text()) or []
    return [Site.model_validate(entry) for entry in raw]


def find_site(sites: list[Site], slug: str) -> Site | None:
    return next((s for s in sites if s.slug == slug), None)
