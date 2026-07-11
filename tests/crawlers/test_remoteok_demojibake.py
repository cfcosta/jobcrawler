"""RemoteOK serves some non-ASCII text double-encoded: UTF-8 bytes decoded as
latin-1 and stored, so "Perú" arrives as "PerÃº" and "São Paulo" as
"SÃ£o Paulo". Left alone, the corrupted city never parses as geography and the
company/title read as garbage in the UI. The crawler reverses the corruption on
the human-readable fields while leaving clean ASCII and already-correct UTF-8
untouched.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import remoteok as crawler


def _mojibake(correct: str) -> str:
    # Reproduce RemoteOK's exact corruption so the fixtures can't drift from the
    # bytes the crawler must reverse: UTF-8 bytes read back as latin-1.
    return correct.encode("utf-8").decode("latin-1")


class _Ctx:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def fetch(self, url: str) -> str:
        return self._payload


def _job(**over) -> dict:
    base = {
        "id": 1,
        "position": "Engineer",
        "company": "Acme",
        "description": "<p>Build things.</p>",
        "location": "Remote",
        "url": "https://remoteok.com/remote-jobs/1",
        "epoch": 0,
    }
    base.update(over)
    return base


def _crawl(jobs: list[dict]) -> dict[str, dict]:
    # Element 0 is RemoteOK's legal notice, which crawl() skips.
    payload = json.dumps([{"legal": "notice"}, *jobs])
    return {j["url"]: j for j in crawler.crawl(_Ctx(payload))}


def test_double_encoded_location_is_repaired() -> None:
    url = "https://remoteok.com/remote-jobs/1"
    jobs = _crawl([_job(id=1, url=url, location=_mojibake("Santa Anita, Perú"))])
    assert jobs[url]["location"] == "Santa Anita, Perú"


def test_double_encoded_company_and_title_are_repaired() -> None:
    url = "https://remoteok.com/remote-jobs/2"
    jobs = _crawl(
        [
            _job(
                id=2,
                url=url,
                position=_mojibake("São Paulo Lead"),
                company=_mojibake("Café Inc"),
            )
        ]
    )
    job = jobs[url]
    assert job["title"] == "São Paulo Lead"
    assert job["company"] == "Café Inc"


def test_double_encoded_description_is_repaired() -> None:
    url = "https://remoteok.com/remote-jobs/3"
    body = _mojibake("<p>Trabajá en español.</p>")
    jobs = _crawl([_job(id=3, url=url, description=body)])
    assert jobs[url]["description"] == "Trabajá en español."


def test_clean_ascii_is_untouched() -> None:
    url = "https://remoteok.com/remote-jobs/4"
    jobs = _crawl(
        [
            _job(
                id=4,
                url=url,
                location="San Francisco",
                company="Acme",
                position="Engineer",
            )
        ]
    )
    job = jobs[url]
    assert job["location"] == "San Francisco"
    assert job["company"] == "Acme"
    assert job["title"] == "Engineer"


def test_already_correct_utf8_is_untouched() -> None:
    # A row that is already single-encoded UTF-8 must survive: "Perú" fails the
    # latin-1 -> UTF-8 round trip (0xFA is not valid standalone UTF-8), so the
    # repair no-ops rather than mangling it.
    url = "https://remoteok.com/remote-jobs/5"
    jobs = _crawl([_job(id=5, url=url, location="Perú", company="Comünïty")])
    job = jobs[url]
    assert job["location"] == "Perú"
    assert job["company"] == "Comünïty"
