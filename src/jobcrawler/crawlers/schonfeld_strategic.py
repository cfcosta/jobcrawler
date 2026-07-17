
import json
import re
import html
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/schonfeld/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _job_obj(fields):
    try:
        return Job(**fields)
    except NameError:
        return fields


def _add_pay(fields, ranges):
    if not ranges:
        return
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return

    mins = []
    maxs = []
    periods = []
    labels = []
    for r in ranges:
        if r.get("min_cents") is not None:
            mins.append(r["min_cents"] / 100)
        if r.get("max_cents") is not None:
            maxs.append(r["max_cents"] / 100)
        period = r.get("period")
        if period:
            periods.append(period)
        label = r.get("range")
        if label:
            labels.append(label)

    if mins:
        fields["salary_min"] = min(mins)
    if maxs:
        fields["salary_max"] = max(maxs)
    fields["salary_currency"] = next(iter(currencies))
    if periods and len(set(periods)) == 1:
        fields["salary_period"] = periods[0]
    if labels:
        fields["salary"] = "; ".join(labels)


def crawl(ctx):
    url = SITE
    if "pay_transparency=true" not in url:
        url += ("&" if "?" in url else "?") + "pay_transparency=true"

    data = json.loads(ctx.fetch(url))
    for raw in data.get("jobs", []):
        title = raw.get("title")
        description = _clean_html(raw.get("content"))
        location = (raw.get("location") or {}).get("name")
        if not title or not description or not location:
            continue

        fields = {
            "url": raw.get("absolute_url") or SITE,
            "title": title,
            "company": "Schonfeld",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(raw.get("id")),
        }

        offices = raw.get("offices") or []
        locations = [office.get("name") for office in offices if office.get("name")]
        if locations:
            fields["locations"] = locations

        departments = raw.get("departments") or []
        dept_names = [dept.get("name") for dept in departments if dept.get("name")]
        if dept_names:
            fields["department"] = " / ".join(dept_names)

        if raw.get("first_published"):
            fields["posted_at"] = raw.get("first_published")
        if raw.get("application_deadline"):
            fields["expires_at"] = raw.get("application_deadline")

        _add_pay(fields, raw.get("pay_input_ranges") or [])

        yield _job_obj({k: v for k, v in fields.items() if v is not None and v != ""})
