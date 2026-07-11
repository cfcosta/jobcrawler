
import json
import re
import html

SITE = 'https://boards-api.greenhouse.io/v1/boards/singlestore/jobs?content=true'
EXTRA_HOSTS = []

_API_URL = SITE + "&pay_transparency=true"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _names(items):
    out = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            out.append(name)
    return out

def _add_salary(job_out, ranges):
    if not ranges:
        return
    currencies = {r.get("currency_type") for r in ranges if r.get("currency_type")}
    if len(currencies) != 1:
        return
    mins = [r.get("min_cents") for r in ranges if isinstance(r.get("min_cents"), (int, float))]
    maxs = [r.get("max_cents") for r in ranges if isinstance(r.get("max_cents"), (int, float))]
    if mins:
        job_out["salary_min"] = min(mins) / 100
    if maxs:
        job_out["salary_max"] = max(maxs) / 100
    job_out["salary_currency"] = next(iter(currencies))
    # Greenhouse pay_input_ranges on this board are annual ranges when present.
    job_out["salary_period"] = "YEAR"

def crawl(ctx):
    data = json.loads(ctx.fetch(_API_URL))
    for item in data.get("jobs", []):
        title = item.get("title")
        loc = (item.get("location") or {}).get("name")
        desc = _clean_html(item.get("content"))
        url = item.get("absolute_url")
        if not (title and loc and desc and url):
            continue

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "SingleStore",
            "location": loc,
            "description": desc,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)
        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]
        if item.get("requisition_id"):
            job["requisition_id"] = item["requisition_id"]

        departments = _names(item.get("departments"))
        if departments:
            job["department"] = departments[0]

        offices = _names(item.get("offices"))
        if offices:
            job["locations"] = offices

        _add_salary(job, item.get("pay_input_ranges"))
        yield job
