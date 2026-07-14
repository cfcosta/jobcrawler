
import json
import re
from html import unescape
from urllib.parse import quote

SITE = "https://webpros.rec.pro.ukg.net/WEB1501WEBP/JobBoard/1c6cf7c0-2d14-4575-906b-78213c1ced24/JobBoardView/LoadSearchResults"
EXTRA_HOSTS = []

_BOARD = "https://webpros.rec.pro.ukg.net/WEB1501WEBP/JobBoard/1c6cf7c0-2d14-4575-906b-78213c1ced24"


def _plain_text(value):
    text = value or ""
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _detail_record(page):
    marker = "new US.Opportunity.CandidateOpportunityDetail("
    start = page.find(marker)
    if start < 0:
        raise ValueError("UKG detail record was not found")
    start += len(marker)

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(page)):
        char = page[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(page[start:index + 1])
    raise ValueError("UKG detail record was truncated")


def _location_name(item):
    for key in ("LocalizedName", "LocalizedDescription", "LocalizedLocationId"):
        value = item.get(key)
        if value:
            return value

    address = item.get("Address") or {}
    parts = []
    for value in (
        address.get("City"),
        (address.get("State") or {}).get("Name"),
        (address.get("Country") or {}).get("Name"),
    ):
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _search_body(top, skip):
    return {
        "opportunitySearch": {
            "Top": top,
            "Skip": skip,
            "QueryString": "",
            "OrderBy": [],
            "Filters": [],
        },
        "matchCriteria": {},
    }


def crawl(ctx):
    top = 100
    skip = 0
    summaries = []

    while True:
        response = ctx.fetch(SITE, method="POST", json=_search_body(top, skip))
        data = json.loads(response)
        batch = data.get("opportunities") or []
        summaries.extend(batch)
        skip += len(batch)
        total = data.get("totalCount")
        if not batch or (total is not None and skip >= total) or len(batch) < top:
            break

    seen = set()
    for summary in summaries:
        opportunity_id = summary.get("Id")
        if not opportunity_id or opportunity_id in seen:
            continue
        seen.add(opportunity_id)

        detail_url = _BOARD + "/OpportunityDetail?opportunityId=" + quote(opportunity_id)
        response = ctx.fetch(detail_url, method="GET")
        record = _detail_record(response)
        if record.get("OpportunityIsClosed"):
            continue

        locations = [
            value
            for value in (
                _location_name(item) for item in (record.get("Locations") or [])
            )
            if value
        ]
        description = _plain_text(record.get("Description"))
        if not record.get("Title") or not locations or not description:
            continue

        fields = {
            "url": detail_url,
            "title": record["Title"],
            "company": "WebPros",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": opportunity_id,
            "apply_url": _BOARD + "/OpportunityApply?opportunityId=" + quote(opportunity_id),
            "department": record.get("JobCategoryName") or None,
            "posted_at": record.get("PostedDate") or None,
            "employment_type": (
                "Full Time"
                if record.get("FullTime") is True
                else "Part Time"
                if record.get("FullTime") is False
                else None
            ),
            "workplace_type": {
                0: "Hybrid",
                1: "On-site",
                2: "Remote",
            }.get(record.get("JobLocationType")),
        }

        if record.get("PayRangeVisible") and record.get("PayRange"):
            pay_range = record["PayRange"]
            fields.update(
                {
                    "salary_min": pay_range.get("PayRangeMinimum"),
                    "salary_max": pay_range.get("PayRangeMaximum"),
                    "salary_currency": record.get("PayRangeCurrencyCode") or None,
                    "salary_period": "yearly" if record.get("Salaried") else "hourly",
                }
            )

        yield {key: value for key, value in fields.items() if value is not None}
