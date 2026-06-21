"""The POST /news ingest client (§4.7).

Crawlers do not write to the news store directly; each extracted item is POSTed
to bizup-news, which owns dedup + LLM enrichment. The only judgement this client
makes is failure classification, because it decides whether a re-POST is safe:

  * INGESTED  — 2xx; the item was accepted.
  * RETRY     — the request provably never created anything (connection refused,
                DNS failure, or a 4xx the server rejected before inserting). Safe
                to re-attempt on the next crawl: no duplicate row, no duplicate
                LLM spend.
  * TERMINAL  — ambiguous: a 5xx, or a timeout *after* the request was sent. The
                server may already have inserted the row and spent on enrichment,
                so re-POSTing would duplicate that. Terminal for this cycle.

The caller (state store / runner) uses the outcome to decide seen/ingested
bookkeeping and whether the item is re-attempted later.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

import httpx

from bizup_autocrawler import config


class IngestOutcome(Enum):
    INGESTED = "ingested"
    RETRY = "retry"
    TERMINAL = "terminal"


class NewsClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = (base_url or config.news_api_url()).rstrip("/")
        self._client = client or httpx.Client(timeout=timeout)

    def post_item(
        self, title: str, body: str, *, crawler: Mapping[str, str] | None = None
    ) -> IngestOutcome:
        """POST one item to ``/news`` and classify the outcome (see module doc).

        ``crawler`` (``{slug, name, url}``) records which crawler produced the item,
        so the news store can attribute and filter by source; omitted when unset.
        """
        payload: dict[str, object] = {"title": title, "body": body}
        if crawler is not None:
            payload["crawler"] = dict(crawler)
        try:
            resp = self._client.post(f"{self._base}/news", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return IngestOutcome.RETRY  # never reached the server: pre-insert
        except httpx.TransportError:
            return IngestOutcome.TERMINAL  # read/write timeout, protocol: after-send
        if resp.is_success:
            return IngestOutcome.INGESTED
        if 400 <= resp.status_code < 500:
            return IngestOutcome.RETRY  # rejected before any insert / LLM spend
        return IngestOutcome.TERMINAL  # 5xx: row + enrichment may already exist
