
import json
import re
from html import unescape
from typing import Iterator
from urllib.parse import urlencode, urlsplit, urlunsplit
from jobcrawler.runtime.contract import Job




SITE = "https://ats.rippling.com/api/v2/board/halborn-inc/jobs?page=0&pageSize=1000"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return ""
    text = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|li|ul|ol|h[1-6]|section|article|tr)>",
        "\n",
        value,
    )
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def _page_url(page):
    parts = urlsplit(SITE)
    query = urlencode({"page": page, "pageSize": 1000})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _next_data(markup):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        markup,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Rippling detail page has no __NEXT_DATA__ payload")
    raw = match.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(unescape(raw))


def crawl(ctx) -> Iterator[Job]:
    seen = set()
    page = 0

    while True:
        payload = json.loads(ctx.fetch(_page_url(page)))
        items = payload.get("items") or []

        for item in items:
            external_id = item.get("id")
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)

            detail_url = item.get("url")
            if not detail_url:
                continue
            data = _next_data(ctx.fetch(detail_url))
            api_data = data["props"]["pageProps"]["apiData"]
            post = api_data["jobPost"]

            description_parts = []
            description = post.get("description") or {}
            if isinstance(description, dict):
                for section in ("company", "role"):
                    text = _html_to_text(description.get(section))
                    if text:
                        description_parts.append(text)
            else:
                text = _html_to_text(description)
                if text:
                    description_parts.append(text)
            full_description = "\n\n".join(description_parts)

            locations = post.get("workLocations") or api_data.get("workLocations") or []
            locations = [value for value in locations if isinstance(value, str) and value]
            if not locations:
                locations = [
                    value.get("name")
                    for value in (item.get("locations") or [])
                    if isinstance(value, dict) and value.get("name")
                ]
            location = locations[0] if locations else None

            list_locations = item.get("locations") or []
            workplace_type = next(
                (
                    value.get("workplaceType")
                    for value in list_locations
                    if isinstance(value, dict) and value.get("workplaceType")
                ),
                None,
            )
            department_data = post.get("department") or item.get("department") or {}
            department = (
                department_data.get("name")
                if isinstance(department_data, dict)
                else department_data
            )
            employment_data = post.get("employmentType") or {}
            employment_type = (
                employment_data.get("id") or employment_data.get("label")
                if isinstance(employment_data, dict)
                else employment_data
            )

            fields = {
                "url": post.get("url") or detail_url,
                "source": SITE,
                "title": post.get("name") or item.get("name"),
                "company": post.get("companyName") or "Halborn",
                "location": location,
                "description": full_description,
                "external_id": post.get("uuid") or external_id,
                "locations": locations or None,
                "department": department or None,
                "employment_type": employment_type or None,
                "workplace_type": workplace_type or None,
                "posted_at": post.get("createdOn") or None,
            }
            yield {key: value for key, value in fields.items() if value is not None}

        total_pages = payload.get("totalPages")
        if not items or (isinstance(total_pages, int) and page + 1 >= total_pages):
            break
        page += 1
