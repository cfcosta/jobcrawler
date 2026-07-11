
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/dremio/jobs?content=true"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _with_pay_transparency(url):
    if "pay_transparency=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + "pay_transparency=true"


def _clean_list(values):
    out = []
    seen = set()
    for value in values:
        if not value:
            continue
        value = str(value).strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _job(fields):
    return {k: v for k, v in fields.items() if v is not None and v != "" and v != []}


def crawl(ctx):
    data = json.loads(ctx.fetch(_with_pay_transparency(SITE)))
    for item in data.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location_obj = item.get("location") or {}
        location = location_obj.get("name") if isinstance(location_obj, dict) else location_obj
        description = _text_from_html(item.get("content"))

        offices = item.get("offices") or []
        locations = _clean_list(
            office.get("name") for office in offices if isinstance(office, dict)
        )

        departments = item.get("departments") or []
        department_names = _clean_list(
            dept.get("name") for dept in departments if isinstance(dept, dict)
        )

        fields = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "Dremio",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "department": ", ".join(department_names) if department_names else None,
            "apply_url": url,
        }

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            r.get("currency_type")
            for r in ranges
            if isinstance(r, dict) and r.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            mins = [r.get("min_cents") for r in ranges if isinstance(r, dict) and r.get("min_cents") is not None]
            maxs = [r.get("max_cents") for r in ranges if isinstance(r, dict) and r.get("max_cents") is not None]
            if mins:
                fields["salary_min"] = min(mins) / 100
            if maxs:
                fields["salary_max"] = max(maxs) / 100
            fields["salary_currency"] = next(iter(currencies))
            periods = _clean_list(
                r.get("pay_period") or r.get("period")
                for r in ranges
                if isinstance(r, dict)
            )
            if periods:
                fields["salary_period"] = periods[0]

        yield _job(fields)
