
import html
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SITE = "https://boards-api.greenhouse.io/v1/boards/truelayer/jobs?content=true"
EXTRA_HOSTS = []


def _api_url():
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["content"] = "true"
    query["pay_transparency"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?s)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?s)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    names = [item.get("name") for item in (items or []) if isinstance(item, dict) and item.get("name")]
    return names[0] if names else None


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1 or not ranges:
        return {}

    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    out = {"salary_currency": next(iter(currencies))}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxs:
        out["salary_max"] = max(maxs) / 100
    return out


def crawl(ctx):
    data = json.loads(ctx.fetch(_api_url()))
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _clean_html(item.get("content"))
        title = item.get("title")
        url = item.get("absolute_url")
        if not (url and title and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "TrueLayer",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")),
        }

        department = _first_name(item.get("departments"))
        if department:
            job["department"] = department

        offices = [office.get("name") for office in (item.get("offices") or []) if isinstance(office, dict) and office.get("name")]
        if offices:
            job["locations"] = offices

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        job.update(_salary_fields(item))
        yield job
