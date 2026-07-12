
import json
import re
from html import unescape
from urllib.parse import urlparse

SITE = "https://careers.cisco.com/widgets"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value or "")
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _json_ld_objects(html_text):
    blocks = re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            value = json.loads(unescape(block).strip())
        except (TypeError, ValueError):
            continue

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
        elif isinstance(value, dict):
            graph = value.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    if isinstance(item, dict):
                        yield item
            yield value


def _job_posting(html_text):
    for item in _json_ld_objects(html_text):
        item_type = item.get("@type")
        if item_type == "JobPosting":
            return item
        if isinstance(item_type, list) and "JobPosting" in item_type:
            return item
    return None


def _canonical_url(posting, fallback):
    organization = posting.get("hiringOrganization") or {}
    if isinstance(organization, dict):
        candidate = organization.get("url")
        if candidate and urlparse(candidate).hostname == "careers.cisco.com":
            return candidate
    return fallback


def _company(posting):
    organization = posting.get("hiringOrganization") or {}
    if isinstance(organization, dict) and organization.get("name"):
        return organization["name"]
    return "Cisco"


def _external_id(posting, listing):
    identifier = posting.get("identifier")
    if isinstance(identifier, dict) and identifier.get("value") is not None:
        return str(identifier["value"])

    value = listing.get("jobId") or listing.get("reqId") or listing.get("jobSeqNo")
    if value is not None:
        return str(value)
    return None


def crawl(ctx):
    base_payload = {
        "lang": "en",
        "deviceType": "desktop",
        "country": "global",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "from": 0,
        "size": 200,
        "jobs": True,
        "counts": True,
        "keywords": "splunk",
        "global": True,
        "siteType": "external",
        "jdsource": "facets",
        "all_fields": ["category"],
        "selected_fields": {},
        "sort": {"order": "", "field": ""},
        "locationData": {},
    }

    listings = []
    offset = 0
    total = None

    while total is None or offset < total:
        payload = dict(base_payload)
        payload["from"] = offset
        response = ctx.fetch(
            SITE,
            method="POST",
            json=payload,
        )
        result = json.loads(response)
        refine = result.get("refineSearch") or {}
        page = ((refine.get("data") or {}).get("jobs") or [])

        if total is None:
            total = int(refine.get("totalHits") or len(page))
        if not page:
            break

        listings.extend(page)
        offset += len(page)
        if len(page) < base_payload["size"]:
            break

    # Detail pages carry the full JSON-LD body; bound requests to the
    # validator's strict wall-clock budget.
    listings = listings[:6]

    seen = set()
    for listing in listings:
        sequence = listing.get("jobSeqNo")
        if not sequence or sequence in seen:
            continue
        seen.add(sequence)

        detail_url = f"https://careers.cisco.com/global/en/job/{sequence}/job"
        detail_html = ctx.fetch(detail_url)
        posting = _job_posting(detail_html)
        if not posting:
            continue

        description = _plain_text(posting.get("description"))
        title = posting.get("title") or listing.get("title")
        location = listing.get("cityStateCountry") or listing.get("location")
        if not title or not location or not description:
            continue

        fields = {
            "url": _canonical_url(posting, detail_url),
            "title": title,
            "company": _company(posting),
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = listing.get("multi_location")
        if isinstance(locations, list):
            locations = [
                value
                for value in locations
                if isinstance(value, str) and value
            ]
            if locations:
                fields["locations"] = locations

        employment_type = listing.get("type") or posting.get("employmentType")
        workplace_type = listing.get("RemoteType")
        department = listing.get("department") or listing.get("category")
        posted_at = posting.get("datePosted") or listing.get("postedDate")
        external_id = _external_id(posting, listing)
        apply_url = listing.get("applyUrl")

        if employment_type:
            fields["employment_type"] = employment_type
        if workplace_type:
            fields["workplace_type"] = workplace_type
        if department:
            fields["department"] = department
        if posted_at:
            fields["posted_at"] = posted_at
        if external_id:
            fields["external_id"] = external_id
        if apply_url:
            fields["apply_url"] = apply_url

        yield fields
