
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/blankstreet/jobs?content=true"
EXTRA_HOSTS = []


def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}
    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    if not mins and not maxs:
        return {}
    out = {"salary_currency": next(iter(currencies))}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxs:
        out["salary_max"] = max(maxs) / 100
    periods = [r.get("pay_period") or r.get("interval") for r in ranges if r.get("pay_period") or r.get("interval")]
    if periods and len(set(periods)) == 1:
        out["salary_period"] = periods[0]
    return out


def crawl(ctx):
    url = SITE
    if "pay_transparency=" not in url:
        url += "&pay_transparency=true" if "?" in url else "?pay_transparency=true"

    data = json.loads(ctx.fetch(url))
    for item in data.get("jobs") or []:
        title = (item.get("title") or "").strip()
        description = _clean_description(item.get("content"))
        location = ((item.get("location") or {}).get("name") or "").strip()
        job_url = item.get("absolute_url") or item.get("url")
        if not (title and description and location and job_url):
            continue

        record = {
            "url": job_url,
            "title": title,
            "company": "Blank Street",
            "location": location,
            "description": description,
            "source": SITE,
        }

        offices = item.get("offices") or []
        locations = []
        for office in offices:
            loc = office.get("location") or office.get("name")
            if loc and loc not in locations:
                locations.append(loc)
        if locations:
            record["locations"] = locations

        departments = item.get("departments") or []
        dept_names = [d.get("name") for d in departments if d.get("name")]
        if dept_names:
            record["department"] = dept_names[0]

        if item.get("id") is not None:
            record["external_id"] = str(item.get("id"))
        if item.get("first_published"):
            record["posted_at"] = item.get("first_published")
        if item.get("absolute_url"):
            record["apply_url"] = item.get("absolute_url")

        record.update(_salary_fields(item))
        yield record
