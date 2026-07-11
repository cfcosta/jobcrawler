"""web3career resolves each row's Apply tracking chain to the real ATS URL (#130).

The board's job pages carry an "Apply Now" CTA pointing at ``/i/<token>`` where
``token = base64(str(ident))`` written backwards — derivable straight from the
listing row, so the job page never needs fetching. The ``/i/`` interstitial
(served only with a Referer) hides a ``/z/<salted>`` URL inside a
``"<blob>".slice(a, b).split('').reverse().join('')`` expression; ``/z/`` then
302s (again Referer-gated) to the company's actual posting. The crawler undoes
the string obfuscation and captures the 302's Location via ``ctx.resolve`` —
never fetching the foreign ATS page — inside a rotating per-crawl budget window
(``_apply_window``) so ~106 rows × 2 fetches can't blow the page budget.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import web3career

IDENT = "96380"
JOB_PATH = f"/backend-engineer-acme/{IDENT}"
JOB_URL = f"https://web3.career{JOB_PATH}"
I_URL = "https://web3.career/i/=ADOzYTO"
Z_PATH = "/z/34c1efffd2dd4c52877585fc5218b0d2OTYzODA="
ATS_URL = (
    "https://job-boards.greenhouse.io/acme/jobs/8611133002"
    "?source=web3.career&gh_src=web3.career#application_form"
)


def _listing() -> str:
    posting = {
        "@type": "JobPosting",
        "title": "Backend Engineer",
        "description": "<p>Build settlement infrastructure in Rust.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressLocality": "Remote"},
        },
    }
    return (
        '<script type="application/ld+json">%s</script>'
        "<tr onclick=\"tableTurboRowClick(event, '%s')\"></tr>"
        % (json.dumps(posting), JOB_PATH)
    )


def _interstitial(dest: str, *, prefix: str = "xK9") -> str:
    # The real page: setTimeout(() => { V="<blob>".slice(a,b).split('')
    # .reverse().join(''); window.location.href = V; }, 33)
    embedded = dest[::-1]
    blob = prefix + embedded + "trailingjunk"
    a, b = len(prefix), len(prefix) + len(embedded)
    return (
        "<html><script>setTimeout(() => {WvQ=\"%s\".slice(%d, %d)"
        ".split('').reverse().join('');window.location.href = WvQ;}, 33)"
        "</script></html>" % (blob, a, b)
    )


class _Ctx:
    """Serves listing page 1, the interstitial, and the /z/ resolution."""

    def __init__(
        self,
        interstitial: str | None,
        resolved: str | Exception = ATS_URL,
    ) -> None:
        self.interstitial = interstitial
        self.resolved = resolved
        self.fetches: list[tuple[str, dict | None]] = []
        self.resolves: list[tuple[str, dict | None]] = []

    def fetch(self, url: str, *, headers=None) -> str:
        self.fetches.append((url, dict(headers) if headers else None))
        if url == web3career.LISTINGS[0]:
            return _listing()
        if url == web3career.LISTINGS[1]:
            return "<html></html>"  # no blocks: page 2 contributes nothing
        if url == I_URL:
            if self.interstitial is None:
                raise AssertionError("interstitial fetched when not expected")
            return self.interstitial
        raise AssertionError(f"unexpected fetch: {url}")

    def resolve(self, url: str, *, headers=None) -> str:
        self.resolves.append((url, dict(headers) if headers else None))
        if isinstance(self.resolved, Exception):
            raise self.resolved
        return self.resolved


def test_interstitial_path_is_reversed_base64_of_the_ident() -> None:
    assert web3career._interstitial_path(IDENT) == "/i/=ADOzYTO"


def test_crawl_captures_the_resolved_ats_url() -> None:
    ctx = _Ctx(_interstitial(Z_PATH))
    jobs = list(web3career.crawl(ctx))

    assert len(jobs) == 1
    assert jobs[0]["apply_url"] == ATS_URL
    # each Referer-gated hop carried the page that links it
    assert (I_URL, {"Referer": JOB_URL}) in ctx.fetches
    assert ctx.resolves == [
        ("https://web3.career" + Z_PATH, {"Referer": I_URL})
    ]


def test_blob_escapes_are_undone_before_slicing() -> None:
    # slice() indices address the DECODED JS string: a blob carrying \" escapes
    # before the destination must not shift the window.
    embedded = Z_PATH[::-1]
    decoded_prefix = 'ab"cd'
    blob_js = 'ab\\"cd' + embedded + "tail"
    a, b = len(decoded_prefix), len(decoded_prefix) + len(embedded)
    page = (
        "<script>V=\"%s\".slice(%d, %d).split('').reverse().join('');"
        "window.location.href = V;</script>" % (blob_js, a, b)
    )
    ctx = _Ctx(page)
    jobs = list(web3career.crawl(ctx))
    assert jobs[0]["apply_url"] == ATS_URL


def test_bounced_interstitial_omits_apply_url() -> None:
    # No Referer / bot defense tripped: /i/ serves the job page again, the
    # slice-reverse expression is absent, and the record ships uncaptured.
    ctx = _Ctx("<html>the job page again</html>")
    jobs = list(web3career.crawl(ctx))
    assert len(jobs) == 1
    assert "apply_url" not in jobs[0]
    assert ctx.resolves == []


def test_same_site_resolution_is_not_an_apply_url() -> None:
    # /z/ bouncing back on-site means the chain never reached the ATS.
    ctx = _Ctx(_interstitial(Z_PATH), resolved=JOB_URL)
    jobs = list(web3career.crawl(ctx))
    assert "apply_url" not in jobs[0]


def test_resolution_failure_still_yields_the_record() -> None:
    ctx = _Ctx(_interstitial(Z_PATH), resolved=RuntimeError("tracker down"))
    jobs = list(web3career.crawl(ctx))
    assert len(jobs) == 1
    assert "apply_url" not in jobs[0]


def test_apply_window_takes_everything_when_it_fits() -> None:
    assert web3career._apply_window(3, 60, seed=12345) == {0, 1, 2}
    assert web3career._apply_window(0, 60, seed=1) == set()


def test_apply_window_rotates_daily_and_tiles_the_whole_list() -> None:
    count, budget = 106, 60
    windows = [web3career._apply_window(count, budget, s) for s in (7000, 7001)]
    assert all(len(w) == budget for w in windows)
    assert windows[0] != windows[1]  # a different slice each day
    covered = set().union(*(
        web3career._apply_window(count, budget, s) for s in range(7000, 7004)
    ))
    assert covered == set(range(count))  # a few days cover every row
