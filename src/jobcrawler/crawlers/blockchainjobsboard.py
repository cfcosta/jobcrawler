
import html
import json
import re

SITE = "https://www.blockchainjobsboard.com/"
EXTRA_HOSTS = []

API_URL = "https://www.blockchainjobsboard.com/wp-json/wp/v2/job-listings?per_page=100"

def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|ul|ol)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _clean(value):
    if value is None:
        return None
    value = html.unescape(str(value)).strip()
    return value or None

def _title(item):
    title = item.get("title") or {}
    if isinstance(title, dict):
        return _strip_tags(title.get("rendered"))
    return _strip_tags(title)

def _description(item):
    content = item.get("content") or {}
    if isinstance(content, dict):
        return _strip_tags(content.get("rendered"))
    return _strip_tags(content)

def _salary(meta):
    amount = _clean(meta.get("_job_salary"))
    if not amount:
        return None
    parts = [amount]
    currency = _clean(meta.get("_job_salary_currency"))
    unit = _clean(meta.get("_job_salary_unit"))
    if currency:
        parts.append(currency)
    if unit:
        parts.append("per " + unit)
    return " ".join(parts)

def crawl(ctx):
    rows = json.loads(ctx.fetch(API_URL))
    seen = set()

    for item in rows:
        if item.get("status") != "publish":
            continue

        url = _clean(item.get("link"))
        if not url or url in seen:
            continue
        seen.add(url)

        meta = item.get("meta") or {}
        title = _title(item)
        description = _description(item)
        company = _clean(meta.get("_company_name"))
        location = _clean(meta.get("_job_location"))

        if not (title and description and company and location):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        posted_at = _clean(item.get("date_gmt") or item.get("date"))
        if posted_at:
            job["posted_at"] = posted_at

        apply_url = _clean(meta.get("_application"))
        if apply_url:
            job["apply_url"] = apply_url

        if str(meta.get("_remote_position", "")).strip() in ("1", "true", "True"):
            job["workplace_type"] = "TELECOMMUTE"

        salary = _salary(meta)
        if salary:
            job["salary"] = salary

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield job
