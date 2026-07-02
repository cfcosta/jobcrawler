"""Enable ``python -m jobcrawler`` (used by the out-of-band optimize dispatch).

The ``jobcrawl`` console script (pyproject ``[project.scripts]``) is the human entry
point; this mirror lets internal callers spawn the CLI via ``sys.executable -m
jobcrawler …`` without depending on the console script being on PATH — which
is how :func:`jobcrawler.optimize_job.dispatch_optimize` hands the §4.11 compile
off to a detached subprocess.
"""

from __future__ import annotations

import sys

from jobcrawler.cli import main

if __name__ == "__main__":
    sys.exit(main())
