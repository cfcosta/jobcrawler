
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/super/jobs?content=true"
EXTRA_HOSTS = []


def _clean_text(value):
    if not value:
        return None
    text = value
    # Greenhouse board content is HTML entity escaped; some boards double-escape ampersands.
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _metadata(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return str(item.get("value"))
    return None


def _department(job):
    names = [d.get("name") for d in (job.get("departments") or []) if d.get("name")]
    if names:
        return ", ".join(names)
    return _metadata(job, "Team")


def _locations(job):
    names = []
    for office in job.get("offices") or []:
        name = office.get("name")
        if name and name not in names:
            names.append(name)
    return names or None


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}

    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}

    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxes = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    if not mins and not maxes:
        return {}

    out = {"salary_currency": next(iter(currencies))}
    if mins:
        out["salary_min"] = min(mins) / 100
    if maxes:
        out["salary_max"] = max(maxes) / 100

    periods = [r.get("pay_period") or r.get("interval") for r in ranges if r.get("pay_period") or r.get("interval")]
    if periods:
        out["salary_period"] = str(periods[0])
    return out


def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    data = json.loads(ctx.fetch(url))
    for job in data.get("jobs") or []:
        title = job.get("title")
        posting_url = job.get("absolute_url")
        location_obj = job.get("location") or {}
        location = location_obj.get("name")
        description = _clean_text(job.get("content"))

        if not (title and posting_url and location and description):
            continue

        item = {
            "url": posting_url,
            "title": title,
            "company": job.get("company_name") or "Super",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
            "posted_at": job.get("first_published"),
            "department": _department(job),
            "employment_type": _metadata(job, "Employment Type"),
        }

        deadline = job.get("application_deadline")
        if deadline:
            item["expires_at"] = deadline

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        item.update(_salary_fields(job))

        yield item
