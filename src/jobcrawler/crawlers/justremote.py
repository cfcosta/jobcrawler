
import json
import re
import html

SITE = "https://justremote-api.herokuapp.com/api/v1/jobs?category=developer"
EXTRA_HOSTS = []

DETAIL_BASE = "https://justremote-api.herokuapp.com/api/v1/jobs/"

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _locations(detail, summary):
    locs = []
    source_locations = detail.get("country_list") or summary.get("location_restrictions") or []
    for item in source_locations:
        val = None
        if isinstance(item, dict):
            val = item.get("label") or item.get("name")
        else:
            val = item
        if val and str(val) not in locs:
            locs.append(str(val))
    for key in ("job_formatted_location", "job_location", "job_country"):
        val = detail.get(key) or summary.get(key)
        if val and str(val) not in locs:
            locs.append(str(val))
    return locs

def _parse_salary(text):
    if not text:
        return {}
    m = re.search(r"(?i)\b(?:pay range|salary range|compensation range|salary|compensation)\s*:?\s*([^\n]{0,120})", text)
    if not m:
        return {}
    printed = m.group(0).strip(" .")
    segment = m.group(1)
    currency = None
    if "$" in segment or "$" in printed:
        currency = "USD"
    nums = re.findall(r"\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*([kK])?", segment)
    vals = []
    for num, suffix in nums[:2]:
        val = float(num.replace(",", ""))
        if suffix:
            val *= 1000
        if val.is_integer():
            vals.append(int(val))
        else:
            vals.append(val)
    out = {"salary": printed}
    if currency:
        out["salary_currency"] = currency
    if vals:
        out["salary_min"] = vals[0]
        if len(vals) > 1:
            out["salary_max"] = vals[1]
        else:
            out["salary_max"] = vals[0]
        if re.search(r"(?i)\b(hour|hourly|hr)\b", segment):
            out["salary_period"] = "hourly"
        else:
            out["salary_period"] = "year"
    return out

def crawl(ctx):
    summaries = json.loads(ctx.fetch(SITE))
    if isinstance(summaries, dict):
        summaries = summaries.get("jobs") or summaries.get("data") or []
    for summary in summaries:
        if summary.get("is_active") is False:
            continue
        job_id = summary.get("id")
        if not job_id:
            continue

        detail_url = DETAIL_BASE + str(job_id)
        detail = json.loads(ctx.fetch(detail_url))
        if detail.get("is_active") is False:
            continue

        parts = []
        for key in ("about_company", "about_role", "who_looking_for", "our_offer", "apply"):
            cleaned = _clean_html(detail.get(key))
            if cleaned:
                parts.append(cleaned)
        description = "\n\n".join(parts).strip()
        if not description:
            continue

        locations = _locations(detail, summary)
        location = ", ".join(locations) if locations else "Remote"

        item = {
            "url": detail_url,
            "title": detail.get("title") or summary.get("title"),
            "company": detail.get("company_name") or summary.get("company_name"),
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
        }
        if locations:
            item["locations"] = locations
        if detail.get("job_type") or summary.get("job_type"):
            item["employment_type"] = detail.get("job_type") or summary.get("job_type")
        if detail.get("remote_type") or summary.get("remote_type"):
            item["workplace_type"] = detail.get("remote_type") or summary.get("remote_type")
        if detail.get("category") or summary.get("category"):
            item["department"] = detail.get("category") or summary.get("category")
        if detail.get("raw_date"):
            item["posted_at"] = detail.get("raw_date")
        if detail.get("valid_through"):
            item["expires_at"] = detail.get("valid_through")

        apply_url = _clean_html(detail.get("apply"))
        if apply_url and re.match(r"^https?://", apply_url):
            item["apply_url"] = apply_url

        salary_fields = _parse_salary(_clean_html(detail.get("our_offer")) or "")
        item.update(salary_fields)

        clean_item = {}
        for key, val in item.items():
            if val is not None and val != "":
                clean_item[key] = val
        yield clean_item
