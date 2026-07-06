"""The position-tracking web UI (§16): ``jobcrawl serve``.

A separate long-running FastAPI process over the same §4.7 state store the daemon owns —
browse/search/filter crawled positions, move them through your personal application
pipeline (`jobcrawler.tracking`), take notes, and fire manual crawl/rescore triggers. See
:mod:`jobcrawler.web.app` for the request surface and the store/daemon boundaries.
"""

from __future__ import annotations

from jobcrawler.web.app import create_app, serve_app

__all__ = ["create_app", "serve_app"]
