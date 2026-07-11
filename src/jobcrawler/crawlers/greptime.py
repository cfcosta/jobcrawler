
import html
import re
from urllib.parse import urljoin

SITE = "https://www.greptime.com/careers"
EXTRA_HOSTS = []


def _clean_text(raw):
    raw = re.sub(r'<a\b[^>]*class="header-anchor"[\s\S]*?</a>', '', raw, flags=re.I)
    raw = re.sub(r'<li\b[^>]*>', '\n- ', raw, flags=re.I)
    raw = re.sub(r'</(p|h1|h2|h3|h4|li|ul|ol|div)>', '\n', raw, flags=re.I)
    raw = re.sub(r'<br\s*/?>', '\n', raw, flags=re.I)
    raw = re.sub(r'<[^>]+>', '', raw)
    raw = html.unescape(raw)
    raw = re.sub(r'[ \t\r\f\v]+', ' ', raw)
    raw = re.sub(r' *\n *', '\n', raw)
    raw = re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()


def _heading_text(raw):
    raw = re.sub(r'<a\b[^>]*class="header-anchor"[\s\S]*?</a>', '', raw, flags=re.I)
    return _clean_text(raw)


def _decode_cfemail(value):
    try:
        data = bytes.fromhex(value)
        key = data[0]
        return ''.join(chr(b ^ key) for b in data[1:])
    except Exception:
        return None


def _extract_summary(block):
    m = re.search(r'<p>\s*<strong>([\s\S]*?)</strong>\s*</p>', block, flags=re.I)
    return _clean_text(m.group(1)) if m else None


def _primary_location(summary):
    if not summary:
        return None
    # The first sentence is the location/workplace summary on this board.
    first = re.split(r'\.\s+', summary, 1)[0].strip()
    return first or summary


def _employment_type(summary):
    if not summary:
        return None
    if re.search(r'\bfull[- ]time\b', summary, flags=re.I):
        return "Full-time"
    return None


def _salary_fields(text):
    fields = {}
    m = re.search(r'US:\s*(\$[\d,]+)\s*[–-]\s*(\$[\d,]+)\s*base\s*\+\s*equity[^.\n]*', text)
    if m:
        fields["salary"] = m.group(0).strip()
        fields["salary_min"] = int(m.group(1).replace("$", "").replace(",", ""))
        fields["salary_max"] = int(m.group(2).replace("$", "").replace(",", ""))
        fields["salary_currency"] = "USD"
        fields["salary_period"] = "year"
    return fields


def crawl(ctx):
    page = ctx.fetch(SITE)

    email = None
    cf = re.search(r'data-cfemail="([0-9a-fA-F]+)"', page)
    if cf:
        decoded = _decode_cfemail(cf.group(1))
        if decoded:
            email = "mailto:" + decoded

    matches = list(re.finditer(r'<h2\b([^>]*)id="([^"]+)"([^>]*)>([\s\S]*?)</h2>', page, flags=re.I))
    for i, match in enumerate(matches):
        job_id = html.unescape(match.group(2)).strip()
        title = _heading_text(match.group(4))
        if not title or job_id == "apply" or title.lower() == "apply":
            continue

        start = match.end()
        next_h2 = matches[i + 1].start() if i + 1 < len(matches) else len(page)
        apply_section = re.search(r'<h3\b[^>]*id="apply"[^>]*>', page[start:next_h2], flags=re.I)
        end = start + apply_section.start() if apply_section else next_h2
        body = page[start:end]

        summary = _extract_summary(body)
        full_text = _clean_text(match.group(0) + body)
        if not full_text:
            continue

        job = {
            "url": SITE + "#" + job_id,
            "title": title,
            "company": "Greptime",
            "location": _primary_location(summary) or "Remote",
            "description": full_text,
            "source": SITE,
            "external_id": job_id,
        }

        if summary:
            job["workplace_type"] = "Remote" if "remote" in summary.lower() else None
            emp = _employment_type(summary)
            if emp:
                job["employment_type"] = emp
        if email:
            job["apply_url"] = email

        job.update(_salary_fields(full_text))
        yield {k: v for k, v in job.items() if v is not None and v != ""}
