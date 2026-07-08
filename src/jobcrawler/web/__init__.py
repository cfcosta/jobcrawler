"""The position-tracking web UI (§16), served in-process by ``jobcrawl run``.

A FastAPI app over the same §4.7 state store the scheduler owns — browse/search/filter
crawled positions, move them through your personal application pipeline
(`jobcrawler.tracking`), take notes, and fire manual crawl/rescore triggers. ``jobcrawl
run`` mounts it on the scheduler's event loop and store, so a "crawl now" request is drained
by that scheduler directly. See :mod:`jobcrawler.web.app` for the request surface and the
store/daemon boundaries.
"""

from __future__ import annotations

from jobcrawler.web.app import create_app

__all__ = ["create_app"]
