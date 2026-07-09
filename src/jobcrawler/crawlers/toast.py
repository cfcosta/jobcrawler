
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/toast/jobs?content=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)

    # Preserve useful block boundaries before removing all remaining tags.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|section|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|ul|ol|section|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n(3,)", "\n\n", text)
    return text.strip() or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs", []):
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        location = _clean((item.get("location") or {}).get("name"))
        description = _html_to_text(item.get("content"))
        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Toast",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        posted_at = _clean(item.get("first_published"))
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = _clean(item.get("application_deadline"))
        if expires_at:
            job["expires_at"] = expires_at

        departments = item.get("departments") or []
        department_names = []
        for department in departments:
            if isinstance(department, dict):
                name = _clean(department.get("name"))
                if name:
                    department_names.append(name)
        if department_names:
            job["department"] = ", ".join(department_names)

        ranges = item.get("pay_input_ranges") or []
        bands = [
            r for r in ranges
            if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
        ]
        currencies = {
            str(r.get("currency_type") or "").strip().upper() for r in bands
        }
        # Single-currency boards only: mixed-currency ranges (one per region)
        # cannot collapse into one honest band, so leave those unstated.
        if bands and len(currencies) == 1 and "" not in currencies:
            job["salary_min"] = min(r["min_cents"] for r in bands) / 100
            job["salary_max"] = max(r["max_cents"] for r in bands) / 100
            job["salary_currency"] = next(iter(currencies))

        yield job
