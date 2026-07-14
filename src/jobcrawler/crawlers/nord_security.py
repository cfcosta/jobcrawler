
import json
import re
from datetime import datetime, timezone
from html import unescape


SITE = "https://nordsecurity.com/careers"
EXTRA_HOSTS = []


def _next_data(document):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return json.loads(match.group(1))


def _plain_text(markup):
    if not markup:
        return None
    text = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\b[^>]*>",
        "\n",
        markup,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    value = "\n".join(line for line in lines if line)
    return value or None


def _posted_at(epoch_ms):
    if not isinstance(epoch_ms, (int, float)):
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, timezone.utc).isoformat()


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    if compensation.get("shouldDisplayCompensation") is not True:
        return {}

    components = [
        component
        for component in (compensation.get("summaryComponents") or [])
        if component.get("compensationType") == "Salary"
    ]
    currencies = {
        component.get("currencyCode")
        for component in components
        if component.get("currencyCode")
    }
    if not components or len(currencies) != 1:
        return {}

    component = components[0]
    result = {
        "salary": compensation.get("compensationTierSummary")
        or component.get("summary"),
        "salary_min": component.get("minValue"),
        "salary_max": component.get("maxValue"),
        "salary_currency": component.get("currencyCode"),
        "salary_period": component.get("interval"),
    }
    return {
        key: value for key, value in result.items()
        if value is not None and value != ""
    }


def _fetch_detail(ctx, listing, build_id):
    external_id = listing.get("id")
    url = SITE.rstrip("/") + "/" + external_id
    data_url = (
        "https://nordsecurity.com/_next/data/"
        + build_id
        + "/careers/"
        + external_id
        + ".json"
    )
    detail_state = json.loads(ctx.fetch(data_url))
    job = detail_state.get("pageProps", {}).get("job") or {}

    title = job.get("text") or listing.get("text")
    description = _plain_text(job.get("description"))
    categories = job.get("categories") or listing.get("categories") or {}
    locations = [
        value for value in (categories.get("allLocations") or [])
        if isinstance(value, str) and value.strip()
    ]
    if not title or not description or not locations:
        return None

    fields = {
        "url": url,
        "source": SITE,
        "title": title,
        "company": "Nord Security",
        "location": locations[0],
        "locations": locations,
        "description": description,
        "external_id": job.get("id") or external_id,
        "employment_type": categories.get("commitment"),
        "workplace_type": job.get("workplaceType")
        or listing.get("workplaceType"),
        "department": categories.get("department"),
        "posted_at": _posted_at(job.get("createdAt")),
        "apply_url": url + "#apply",
    }
    fields.update(_salary_fields(job))
    return {
        key: value for key, value in fields.items()
        if value is not None and value != ""
    }


def crawl(ctx):
    state = _next_data(ctx.fetch(SITE))
    if not state:
        return

    build_id = state.get("buildId")
    teams = (
        state.get("props", {})
        .get("pageProps", {})
        .get("pageData", {})
        .get("jobsData", {})
        .get("jobsByTeam", [])
    )
    if not build_id:
        return

    seen = set()
    remaining = 24
    for team in teams:
        for listing in team.get("items", []):
            if remaining <= 0:
                return
            remaining -= 1
            external_id = listing.get("id")
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)

            fields = _fetch_detail(ctx, listing, build_id)
            if fields:
                yield fields
