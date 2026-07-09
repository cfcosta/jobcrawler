
import html
import json
import re

SITE = "https://consensys.io/careers"
EXTRA_HOSTS = []

def _decode_js_string(raw):
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return raw.encode("utf-8", "ignore").decode("unicode_escape", "ignore")

def _field_from_job_detail(page, job_id, field):
    start = page.find("jobDetail:{id:" + str(job_id) + ",")
    if start < 0:
        return None
    section = page[start:]
    match = re.search(r'\b' + re.escape(field) + r':"((?:\\.|[^"\\])*)"', section)
    if not match:
        return None
    return _decode_js_string(match.group(1))

def _strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)(?:\s[^>]*)?>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip()

def _clean(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value or None

def _salary_from_description(text):
    match = re.search(
        r"US pay range[^\n]*(?:\n|.){0,250}?(\$[\d,]+)\s*(?:—|-|to)\s*(\$[\d,]+)\s*(USD)?",
        text,
        re.I,
    )
    if not match:
        match = re.search(r"(\$[\d,]+)\s*(?:—|-|to)\s*(\$[\d,]+)\s*(USD)", text, re.I)
    if not match:
        return None
    currency = " USD" if (match.group(3) or "").upper() == "USD" else ""
    return match.group(1) + " - " + match.group(2) + currency

def _detail_urls_from_sitemap(ctx):
    index = ctx.fetch("https://consensys.io/sitemap.xml")
    sitemap_urls = re.findall(r"<loc>\s*(https://consensys\.io/sitemap_[^<]+\.xml)\s*</loc>", index)
    seen = set()
    for sitemap_url in sitemap_urls:
        xml = ctx.fetch(sitemap_url)
        for url in re.findall(r"<loc>\s*(https://consensys\.io/open-roles/\d+)\s*</loc>", xml):
            if url not in seen:
                seen.add(url)
                yield url

def crawl(ctx):
    for url in _detail_urls_from_sitemap(ctx):
        job_id = url.rstrip("/").rsplit("/", 1)[-1]
        page = ctx.fetch(url)

        title = _clean(_field_from_job_detail(page, job_id, "title"))
        location = _clean(_field_from_job_detail(page, job_id, "location"))
        employment_type = _clean(_field_from_job_detail(page, job_id, "employeeTime"))
        department = _clean(_field_from_job_detail(page, job_id, "department"))
        description = _strip_html(_field_from_job_detail(page, job_id, "content"))

        if not (title and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Consensys",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job_id,
        }

        if employment_type:
            job["employment_type"] = employment_type
        if department:
            job["department"] = department

        apply_match = re.search(r'<a\s+href="([^"]*job-boards\.greenhouse\.io/embed/job_app\?[^"]*)"', page)
        if apply_match:
            job["apply_url"] = html.unescape(apply_match.group(1))

        salary = _salary_from_description(description)
        if salary:
            job["salary"] = salary

        yield job
