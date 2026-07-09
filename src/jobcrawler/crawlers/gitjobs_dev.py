
import re
import html
from urllib.parse import urljoin

SITE = "https://gitjobs.dev/"
EXTRA_HOSTS = []

def _unescape(value):
    return html.unescape(value or "").strip()

def _strip_tags(fragment):
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment or "")
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|ul|ol)>", "\n", fragment)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def _attrs(tag):
    out = {}
    for name, quote, value in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2', tag or "", re.S):
        out[name] = html.unescape(value)
    return out

def _first(pattern, text, flags=re.S | re.I):
    m = re.search(pattern, text or "", flags)
    return _unescape(m.group(1)) if m else None

def _label_value(label, plain):
    labels = [
        "Published", "Last updated", "Job type", "Workplace", "Seniority level",
        "Location", "Open source time", "Upstream projects time", "Salary",
        "Apply", "Share this job", "Get embed code", "Job description",
        "Responsibilities", "Qualifications", "Required skills", "Benefits",
        "Apply instructions", "About company", "Embed code", "Close modal",
    ]
    alternatives = [re.escape(x) for x in labels if x != label]
    pat = r"\b" + re.escape(label) + r"\s+(.+?)(?=\s+(?:" + "|".join(alternatives) + r")\b|$)"
    m = re.search(pat, plain, re.I | re.S)
    if not m:
        return None
    value = re.sub(r"\s+", " ", m.group(1)).strip(" :-")
    return value or None

def _section_text(heading, detail):
    pattern = (
        r'<div[^>]*class="[^"]*text-lg[^"]*"[^>]*>\s*'
        + re.escape(heading)
        + r'\s*</div>\s*<div[^>]*>(.*?)</div>\s*(?=<div|\Z)'
    )
    m = re.search(pattern, detail, re.I | re.S)
    if not m:
        return None
    text = _strip_tags(m.group(1))
    return text or None

def _description(detail):
    parts = []
    for heading in [
        "Job description",
        "Responsibilities",
        "Qualifications",
        "Required skills",
        "Benefits",
        "Apply instructions",
        "About company",
    ]:
        text = _section_text(heading, detail)
        if text:
            parts.append(heading + "\n" + text)
    if parts:
        return "\n\n".join(parts)

    plain = _strip_tags(detail)
    start = plain.lower().find("job description")
    end = plain.lower().find("embed code")
    if start >= 0:
        body = plain[start:end if end > start else None]
        return body.strip()
    return plain

def _parse_salary(text):
    if not text:
        return None
    if "not provided" in text.lower():
        return None
    return re.sub(r"\s+", " ", text).strip() or None

def crawl(ctx):
    listing = ctx.fetch(urljoin(SITE, "/section/jobs/results"))
    seen = set()

    for button in re.findall(r'<button\b[^>]*data-preview-job="true"[\s\S]*?</button>', listing, re.I):
        open_tag = re.search(r"<button\b[^>]*>", button, re.I | re.S)
        attrs = _attrs(open_tag.group(0) if open_tag else button)

        job_id = attrs.get("data-job-id")
        detail_path = attrs.get("hx-get")
        if not job_id or not detail_path or job_id in seen:
            continue
        seen.add(job_id)

        detail = ctx.fetch(urljoin(SITE, detail_path))
        title = attrs.get("data-job-title") or _first(r'<div[^>]*class="[^"]*font-semibold[^"]*"[^>]*>\s*([^<]+)', detail)
        company = attrs.get("data-job-company")

        plain = _strip_tags(detail)
        if not company:
            # The detail fragment starts with "Close modal Company Title ..."
            bits = plain.split()
            company = " ".join(bits[2:4]).strip() if len(bits) > 4 else None

        location = _label_value("Location", plain)
        if not location:
            location = _first(r"Location</div>\s*<div[^>]*>\s*(.*?)\s*</div>", detail)
            location = _strip_tags(location or "")

        job = {
            "url": SITE.rstrip("/") + "/?job_id=" + job_id,
            "title": _unescape(title),
            "company": _unescape(company),
            "location": _unescape(location) or "Remote",
            "description": _description(detail),
            "source": SITE,
            "external_id": job_id,
        }

        apply_url = _first(r'data-apply-url=["\']([^"\']+)["\']', detail)
        if apply_url:
            job["apply_url"] = apply_url

        posted_at = _label_value("Published", plain)
        if posted_at and re.match(r"\d{4}-\d{2}-\d{2}$", posted_at):
            job["posted_at"] = posted_at

        employment_type = _label_value("Job type", plain)
        if employment_type:
            job["employment_type"] = employment_type

        workplace_type = _label_value("Workplace", plain)
        if workplace_type:
            job["workplace_type"] = workplace_type

        salary = _parse_salary(_label_value("Salary", plain))
        if salary:
            job["salary"] = salary

        yield {k: v for k, v in job.items() if v is not None and v != ""}
