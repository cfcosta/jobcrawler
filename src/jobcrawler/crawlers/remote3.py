
import re
import json
import html
from urllib.parse import urljoin

SITE = "https://www.remote3.co/"
EXTRA_HOSTS = []

def _clean_html(value):
    if not value:
        return None
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<(br|/p|/li|/h[1-6]|/div)\b[^>]*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _rsc_payload(page_html):
    chunks = []
    for match in re.finditer(r"<script>(?:self\.__next_f\.push\((.*?)\))</script>", page_html, flags=re.S):
        try:
            item = json.loads(match.group(1))
        except Exception:
            continue
        if len(item) > 1 and isinstance(item[1], str):
            chunks.append(item[1])
    return "".join(chunks)

def _flight_text_records(payload):
    records = {}
    # Next/React Flight emits long strings as key:T<hex length>,<payload>.
    for match in re.finditer(r"(?m)([0-9a-f]+):T([0-9a-f]+),", payload):
        key = match.group(1)
        size = int(match.group(2), 16)
        start = match.end()
        records[key] = payload[start:start + size]
    return records

def _initial_jobs(payload):
    marker = '"initialJobs":'
    idx = payload.find(marker)
    if idx < 0:
        return []
    start = payload.find("[", idx)
    if start < 0:
        return []
    try:
        jobs, _ = json.JSONDecoder().raw_decode(payload[start:])
        return jobs if isinstance(jobs, list) else []
    except Exception:
        return []

def _total_pages(payload):
    match = re.search(r'"totalPages":(\d+)', payload)
    if not match:
        return 1
    try:
        return max(1, min(int(match.group(1)), 20))
    except Exception:
        return 1

def _salary(job):
    currency = "USD"
    low = job.get("salary_min")
    high = job.get("salary_max")
    if low is None and high is None:
        return None
    if low is not None and high is not None:
        if low == high:
            return "%s %s per year" % (currency, int(low))
        return "%s %s - %s per year" % (currency, int(low), int(high))
    if low is not None:
        return "%s %s per year" % (currency, int(low))
    return "%s %s per year" % (currency, int(high))

def _positive_amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None

def _salary_fields(record):
    """Keep the flattened text AND the structured quartet. Currency and period
    mirror the text builder (USD per year); zero bounds are omitted as unstated."""
    text = _salary(record)
    if not text:
        return {}
    fields = {"salary": text}
    low = _positive_amount(record.get("salary_min"))
    high = _positive_amount(record.get("salary_max"))
    if low is not None:
        fields["salary_min"] = low
    if high is not None:
        fields["salary_max"] = high
    if low is not None or high is not None:
        fields["salary_currency"] = "USD"
        fields["salary_period"] = "year"
    return fields

def _date(value):
    if not value or not isinstance(value, str):
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else None

def _job_from_record(record, records):
    slug = record.get("slug")
    title = record.get("title")
    location = record.get("location")
    company = (record.get("companies") or {}).get("name")
    if not slug or not title or not company or not location:
        return None

    description = record.get("description")
    if isinstance(description, str) and description.startswith("$"):
        description = records.get(description[1:], description)
    description = _clean_html(description)
    if not description:
        return None

    item = {
        "url": urljoin(SITE, "/remote-jobs/" + slug),
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": str(record.get("id") or record.get("uid") or slug),
    }

    for key, value in _salary_fields(record).items():
        item[key] = value
    if record.get("type"):
        item["employment_type"] = record.get("type")
    if record.get("is_worldwide") or re.search(r"\b(remote|worldwide)\b", location, re.I):
        item["workplace_type"] = "Remote"
    posted_at = _date(record.get("live_at") or record.get("created_at"))
    if posted_at:
        item["posted_at"] = posted_at
    if record.get("apply_url"):
        item["apply_url"] = record.get("apply_url")

    return item

def _page_url(page_number):
    if page_number <= 1:
        return SITE
    return urljoin(SITE, "/?page=%d" % page_number)

def crawl(ctx):
    first_html = ctx.fetch(SITE)
    first_payload = _rsc_payload(first_html)
    pages = _total_pages(first_payload)

    seen = set()
    for page in range(1, pages + 1):
        page_html = first_html if page == 1 else ctx.fetch(_page_url(page))
        payload = first_payload if page == 1 else _rsc_payload(page_html)
        records = _flight_text_records(payload)

        for record in _initial_jobs(payload):
            job = _job_from_record(record, records)
            if not job:
                continue
            if job["url"] in seen:
                continue
            seen.add(job["url"])
            yield job
