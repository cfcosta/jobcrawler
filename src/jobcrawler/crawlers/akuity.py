
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/akuity/jobs?content=true'
EXTRA_HOSTS = []


def _response_text(response):
    if hasattr(response, "text"):
        return response.text
    return response


def _clean_description(value):
    if not value:
        return None

    # Greenhouse content is HTML-entity escaped; unescape until it stabilizes.
    text = value
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|ul|ol|section|article|tr)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(name)
    return names


def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_description(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title.strip(),
            "company": "Akuity",
            "location": location,
            "description": description,
            "source": SITE,
        }

        offices = _names(job.get("offices"))
        if offices:
            item["locations"] = offices

        departments = _names(job.get("departments"))
        if departments:
            item["department"] = ", ".join(departments)

        if job.get("first_published"):
            item["posted_at"] = job["first_published"]
        if job.get("id") is not None:
            item["external_id"] = str(job["id"])
        if url:
            item["apply_url"] = url

        ranges = job.get("pay_input_ranges") or []
        currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
        if len(currencies) == 1 and ranges:
            mins = [r.get("min_cents") for r in ranges if r.get("min_cents") is not None]
            maxs = [r.get("max_cents") for r in ranges if r.get("max_cents") is not None]
            if mins:
                item["salary_min"] = min(mins) / 100
            if maxs:
                item["salary_max"] = max(maxs) / 100
            item["salary_currency"] = next(iter(currencies))

        yield item
