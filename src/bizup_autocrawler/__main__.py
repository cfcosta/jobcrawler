"""Enable ``python -m bizup_autocrawler`` (used by the out-of-band optimize dispatch).

The ``autocrawl`` console script (pyproject ``[project.scripts]``) is the human entry
point; this mirror lets internal callers spawn the CLI via ``sys.executable -m
bizup_autocrawler …`` without depending on the console script being on PATH — which
is how :func:`bizup_autocrawler.optimize_job.dispatch_optimize` hands the §4.11 compile
off to a detached subprocess.
"""

from __future__ import annotations

import sys

from bizup_autocrawler.cli import main

if __name__ == "__main__":
    sys.exit(main())
