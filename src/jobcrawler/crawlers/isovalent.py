
import json
import re
from jobcrawler.runtime.contract import Job
from html import unescape
from urllib.parse import quote

SITE = "https://careers.cisco.com/widgets"
EXTRA_HOSTS = []


def _text(value):
    value = unescape(value or "")
    value = re.sub(
        r"<\s*(?:br|p|div|li|h[1-6]|tr|section|article|blockquote|pre|hr)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"</\s*(?:p|div|li|h[1-6]|tr|section|article|blockquote|pre)\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style\s*>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _job_posting(page):
    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    )
    for raw in scripts:
        try:
            value = json.loads(unescape(raw).strip())
        except (TypeError, ValueError):
            continue
        pending = value if isinstance(value, list) else [value]
        while pending:
            item = pending.pop(0)
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "JobPosting":
                return item
            graph = item.get("@graph")
            if isinstance(graph, list):
                pending.extend(graph)
    return None


def _fetch_detail(ctx, url):
    return ctx.fetch(url)


def _fetch_listing(ctx, request):
    return ctx.fetch(SITE, method="POST", json=request)


def crawl(ctx):
    page_size = 100
    offset = 0
    seen = set()

    while True:
        request = {
            "lang": "en_global",
            "deviceType": "desktop",
            "country": "global",
            "ddoKey": "refineSearch",
            "from": offset,
            "size": page_size,
            "jobs": True,
            "counts": True,
            "clientName": "cisco",
            "keywords": "Isovalent",
            "global": True,
            "pageName": "search-results",
        }
        data = json.loads(_fetch_listing(ctx, request))
        result = data.get("refineSearch") or {}
        jobs = (result.get("data") or {}).get("jobs") or []

        for listed in jobs:
            sequence = listed.get("jobSeqNo")
            title = listed.get("title")
            if not sequence or not title or sequence in seen:
                continue
            seen.add(sequence)

            url = (
                "https://careers.cisco.com/global/en/job/"
                + quote(str(sequence), safe="")
                + "/"
                + _slug(title)
            )
            posting = _job_posting(_fetch_detail(ctx, url))
            if not posting:
                continue
            description = _text(posting.get("description"))
            if not description:
                continue

            identifier = posting.get("identifier")
            external_id = listed.get("reqId") or listed.get("jobId")
            if not external_id and isinstance(identifier, dict):
                external_id = identifier.get("value")

            locations = listed.get("multi_location")
            if not isinstance(locations, list):
                locations = None

            fields = {
                "url": url,
                "title": posting.get("title") or title,
                "company": "Cisco",
                "location": listed.get("location") or listed.get("cityStateCountry"),
                "description": description,
                "source": "careers.cisco.com",
                "locations": locations,
                "employment_type": listed.get("type") or (
                    posting.get("employmentType")[0]
                    if isinstance(posting.get("employmentType"), list)
                    and posting.get("employmentType")
                    else posting.get("employmentType")
                ),
                "workplace_type": listed.get("RemoteType"),
                "department": listed.get("department"),
                "posted_at": posting.get("datePosted"),
                "expires_at": posting.get("validThrough"),
                "external_id": external_id,
                "apply_url": listed.get("applyUrl"),
            }
            yield Job(**{
                key: value for key, value in fields.items()
                if value is not None and value != ""
            })

        offset += len(jobs)
        total = result.get("totalHits")
        if not jobs or len(jobs) < page_size:
            break
        if isinstance(total, int) and offset >= total:
            break
