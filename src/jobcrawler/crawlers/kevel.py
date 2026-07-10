
import json
import re
import html

SITE = "https://ats.rippling.com/embed/kevel-careers/jobs"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style|noscript|meta)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|ul|ol|h[1-6]|tr|section|article)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _board_items(page):
    data = _next_data(page)
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        payload = query.get("state", {}).get("data")
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list) and items and all(isinstance(item, dict) for item in items):
                if all(item.get("id") and item.get("url") and item.get("name") for item in items):
                    return items
    return []


def _job_detail(page):
    data = _next_data(page)
    api = data.get("props", {}).get("pageProps", {}).get("apiData", {})
    if not isinstance(api, dict):
        return {}, {}
    return api.get("jobPost") or {}, api.get("jobBoard") or {}


def _description_text(job_post):
    desc = job_post.get("description")
    sections = []
    if isinstance(desc, dict):
        for key in ("company", "role"):
            text = _html_to_text(desc.get(key))
            if text:
                sections.append(text)
        for key, value in desc.items():
            if key not in {"company", "role"} and isinstance(value, str):
                text = _html_to_text(value)
                if text:
                    sections.append(text)
    elif isinstance(desc, str):
        text = _html_to_text(desc)
        if text:
            sections.append(text)
    return "\n\n".join(sections) or None


def _location_names_from_item(item):
    names = []
    for loc in item.get("locations") or []:
        if isinstance(loc, dict) and loc.get("name"):
            names.append(loc["name"])
    return names


def _workplace_type(item):
    values = []
    for loc in item.get("locations") or []:
        if isinstance(loc, dict) and loc.get("workplaceType"):
            values.append(loc["workplaceType"])
    if not values:
        return None
    if len(set(values)) == 1:
        return values[0]
    return ", ".join(values)


def _employment_type(job_post):
    employment = job_post.get("employmentType")
    if isinstance(employment, dict):
        return employment.get("id") or employment.get("label")
    if isinstance(employment, str):
        return employment
    return None


def crawl(ctx):
    board_page = ctx.fetch(SITE)
    for item in _board_items(board_page):
        url = item.get("url")
        if not url:
            continue

        detail_page = ctx.fetch(url)
        job_post, job_board = _job_detail(detail_page)

        title = (job_post.get("name") or item.get("name") or "").strip()
        locations = _location_names_from_item(item) or list(job_post.get("workLocations") or [])
        location = locations[0] if locations else None
        description = _description_text(job_post)

        if not (title and location and description):
            continue

        job = {
            "url": job_post.get("url") or url,
            "title": title,
            "company": job_post.get("companyName") or job_board.get("companyName") or "Kevel",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": job_post.get("uuid") or item.get("id"),
        }

        department = item.get("department") or job_post.get("department")
        if isinstance(department, dict) and department.get("name"):
            job["department"] = department["name"]
        elif isinstance(department, str) and department:
            job["department"] = department

        employment_type = _employment_type(job_post)
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = _workplace_type(item)
        if workplace_type:
            job["workplace_type"] = workplace_type

        if job_post.get("createdOn"):
            job["posted_at"] = job_post["createdOn"]

        yield job
