
import html
import json
import re

SITE = "https://bigcommerce.wd12.myworkdayjobs.com/wday/cxs/bigcommerce/Commerce/jobs"
EXTRA_HOSTS = []

_CXS_BASE = "https://bigcommerce.wd12.myworkdayjobs.com/wday/cxs/bigcommerce/Commerce"
_SOURCE = "bigcommerce"


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

    while total is None or len(postings) < total:
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

    # The sandbox permits only synchronous fetches and has a strict wall timeout.
    for item in postings[:10]:
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
            "company": "Commerce",
            "location": location,
            "description": description,
            "source": _SOURCE,
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

        apply_url = info.get("externalUrl")
        if apply_url:
            job["apply_url"] = apply_url

        yield job
