"""Tests for the crawler contract — the normalized Item (§4.6).

An Item is what a generated crawler yields and the runner validates before
dedup/ingest. ``title`` and ``body`` map to ``POST /news``; ``url`` is the
canonical dedup key; ``source`` is the site slug.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bizup_autocrawler.runtime.contract import Item


def _fields() -> dict[str, str]:
    return {"url": "https://x.com/a", "title": "T", "body": "B", "source": "x"}


def test_item_requires_only_core_fields() -> None:
    item = Item(url="https://x.com/a", title="T", body="B", source="x")
    assert item.url == "https://x.com/a"
    assert item.published_at is None


def test_item_accepts_published_at() -> None:
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    item = Item(url="https://x.com/a", title="T", body="B", source="x", published_at=ts)
    assert item.published_at == ts


@pytest.mark.parametrize("missing", ["url", "title", "body", "source"])
def test_item_rejects_missing_required(missing: str) -> None:
    fields = _fields()
    del fields[missing]
    with pytest.raises(ValidationError):
        Item.model_validate(fields)


@pytest.mark.parametrize("blank", ["url", "title", "body", "source"])
def test_item_rejects_blank_required(blank: str) -> None:
    fields = _fields()
    fields[blank] = ""
    with pytest.raises(ValidationError):
        Item.model_validate(fields)
