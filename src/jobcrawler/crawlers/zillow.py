import html
import json
import re

SITE = "https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External/jobs"
EXTRA_HOSTS = []

_CXS_BASE = "https://zillow.wd5.myworkdayjobs.com/wday/cxs/zillow/Zillow_Group_External"
_SOURCE = "zillow"
_COMPANY = "Zillow"

# Workday CXS needs one detail GET per posting, and every crawl runs under a wall-clock
# ceiling (10s in the validation sandbox). Bound BOTH passes so a giant tenant (Boeing
# lists ~1000 reqs) still finishes: page only the freshest few list pages, then hydrate
# a small sample. The board is registered enumerates_all:false — a windowed slice,
# rechecked per-row by the §4.9 sweep, never diff-closed.
_LIST_PAGES = 3
_DETAIL_CAP = 10


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>|</div\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def crawl(ctx):
    postings = []
    seen_paths = set()
    offset = 0
    total = None
    pages = 0

    # Bounded list pass: Workday returns newest-first, so the first few pages are the
    # freshest openings. Capping the page count keeps a 1000-req board inside the
    # sandbox wall clock.
    while (total is None or offset < total) and pages < _LIST_PAGES:
        page = json.loads(ctx.fetch(
            SITE,
            method="POST",
            json={
                "appliedFacets": {},
                "limit": 20,
                "offset": offset,
                "searchText": "",
            },
        ))
        total = page.get("total", 0)
        items = page.get("jobPostings") or []
        if not items:
            break
        for item in items:
            path = item.get("externalPath")
            if path and path not in seen_paths:
                seen_paths.add(path)
                postings.append(item)
        offset += len(items)
        pages += 1

    yielded = 0
    for item in postings:
        if yielded >= _DETAIL_CAP:
            break
        path = item.get("externalPath")
        if not path:
            continue

        payload = json.loads(ctx.fetch(_CXS_BASE + path))
        info = payload.get("jobPostingInfo") or {}

        if info.get("posted") is False or info.get("canApply") is False:
            continue

        title = info.get("title") or item.get("title")
        description = _text(info.get("jobDescription"))
        location = info.get("location")
        url = info.get("externalUrl")

        if not title or not description or not location or not url:
            continue

        job = {
            "url": url,
            "title": title,
            "company": _COMPANY,
            "location": location,
            "description": description,
            "source": _SOURCE,
            "apply_url": url,
        }

        additional = info.get("additionalLocations") or []
        locations = [location]
        for name in additional:
            if name and name not in locations:
                locations.append(name)
        if len(locations) > 1:
            job["locations"] = locations

        external_id = info.get("jobReqId")
        if external_id:
            job["external_id"] = external_id
        posted_at = info.get("startDate")
        if posted_at:
            job["posted_at"] = posted_at
        employment_type = info.get("timeType")
        if employment_type:
            job["employment_type"] = employment_type
        workplace_type = info.get("remoteType")
        if workplace_type:
            job["workplace_type"] = workplace_type

        yielded += 1
        yield job
