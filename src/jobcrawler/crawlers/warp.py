SITE = 'https://boards-api.greenhouse.io/v1/boards/warp/jobs?content=true'
EXTRA_HOSTS = []

def _text_from_html(value):
    import html
    import re

    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None

def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return {}

    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return {}

    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    fields = {"salary_currency": next(iter(currencies))}
    if mins:
        fields["salary_min"] = min(mins) / 100
    if maxs:
        fields["salary_max"] = max(maxs) / 100

    periods = [r.get("pay_period") or r.get("interval") or r.get("period") for r in ranges]
    periods = [p for p in periods if p]
    if periods:
        fields["salary_period"] = periods[0]

    texts = [r.get("text") or r.get("display") or r.get("label") for r in ranges]
    texts = [t for t in texts if t]
    if texts:
        fields["salary"] = "; ".join(texts)
    return fields

def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response

def crawl(ctx):
    import json

    url = SITE
    if "pay_transparency=true" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "pay_transparency=true"

    data = json.loads(_fetch_text(ctx, url))
    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = item.get("offices") or []
        locations = [office.get("name") for office in offices if office.get("name")]

        departments = item.get("departments") or []
        department_names = [d.get("name") for d in departments if d.get("name")]

        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Warp",
            "location": location,
            "description": _text_from_html(item.get("content")),
            "source": SITE,
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
        }

        if locations:
            job["locations"] = locations
        if department_names:
            job["department"] = ", ".join(department_names)
        if item.get("application_deadline"):
            job["expires_at"] = item.get("application_deadline")
        if item.get("absolute_url"):
            job["apply_url"] = item.get("absolute_url")

        job.update(_salary_fields(item))

        yield {k: v for k, v in job.items() if v is not None and v != ""}
