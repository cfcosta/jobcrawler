import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

SITE = "https://wise.jobs/engineering"
EXTRA_HOSTS = []

SITEMAP_URL = "https://wise.jobs/vacanciessitemap.xml"
COMPANY = "Wise"
SOURCE = "wise"
MAX_POSTINGS = 6

JID_RE = re.compile(r"-jid-(\d+)$")
SALARY_RE = re.compile(
    r"^\s*([\d.,]+)\s*[-–]\s*([\d.,]+)\s+([A-Za-z]{3})\s+([A-Za-z]+)\s*$"
)

PERIODS = {
    "annual": "year",
    "annually": "year",
    "yearly": "year",
    "monthly": "month",
    "weekly": "week",
    "daily": "day",
    "hourly": "hour",
}


def _text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value)
        value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
        value = re.sub(
            r"</\s*(p|div|li|h[1-6]|section|tr|ul|ol)\s*>", "\n", value, flags=re.I
        )
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip() or None
    if isinstance(value, list):
        parts = [_text(v) for v in value]
        return ", ".join(p for p in parts if p) or None
    return str(value).strip() or None


def _find_jobposting(page):
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    ):
        raw = html.unescape(raw).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                typ = item.get("@type")
                if typ == "JobPosting" or (
                    isinstance(typ, list) and "JobPosting" in typ
                ):
                    return item
                if isinstance(item.get("@graph"), list):
                    stack.extend(item["@graph"])
                for v in item.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(item, list):
                stack.extend(item)
    return None


def _location(job):
    loc = job.get("jobLocation")
    if not isinstance(loc, list):
        loc = [loc] if loc else []
    parts = []
    for entry in loc:
        if isinstance(entry, str):
            val = entry.strip()
            if val:
                parts.append(val)
            continue
        if not isinstance(entry, dict):
            continue
        address = entry.get("address", entry)
        if isinstance(address, str):
            vals = [address]
        elif isinstance(address, dict):
            vals = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
        else:
            vals = []
        text = ", ".join(str(v).strip() for v in vals if v)
        if text:
            parts.append(text)
    remote = job.get("jobLocationType")
    if remote and str(remote).strip() not in parts:
        parts.append(str(remote).strip())
    seen = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "; ".join(seen) if seen else None


def _to_float(value):
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _salary(job):
    benefits = job.get("jobBenefits")
    if isinstance(benefits, list):
        benefits = ", ".join(str(b).strip() for b in benefits if b)
    if not isinstance(benefits, str):
        return {}
    raw = html.unescape(benefits).strip()
    if not raw:
        return {}
    out = {"salary": raw}
    match = SALARY_RE.match(raw)
    if match:
        low = _to_float(match.group(1))
        high = _to_float(match.group(2))
        if low is not None:
            out["salary_min"] = low
        if high is not None:
            out["salary_max"] = high
        out["salary_currency"] = match.group(3).upper()
        period = match.group(4).lower()
        out["salary_period"] = PERIODS.get(period, period)
    return out


def _external_id(url, job):
    ident = job.get("identifier")
    if isinstance(ident, dict) and ident.get("value"):
        return str(ident["value"]).strip() or None
    match = JID_RE.search(urlparse(url).path)
    if match:
        return "wise-" + match.group(1)
    return None


def _urls_from_sitemap(xml):
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []
    items = []
    for idx, url_el in enumerate(root.findall(".//{*}url")):
        loc_el = url_el.find("{*}loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        if not JID_RE.search(urlparse(loc).path):
            continue
        lastmod_el = url_el.find("{*}lastmod")
        lastmod = (
            lastmod_el.text.strip()
            if lastmod_el is not None and lastmod_el.text
            else ""
        )
        items.append((lastmod, -idx, loc))
    items.sort(reverse=True)
    return [loc for _, __, loc in items]


def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    for url in _urls_from_sitemap(xml)[:MAX_POSTINGS]:
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        job = _find_jobposting(page)
        if not job:
            continue
        title = _text(job.get("title"))
        description = _text(job.get("description"))
        location = _location(job)
        if not (title and description and location):
            continue
        out = {
            "url": _text(job.get("url")) or url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SOURCE,
            "external_id": _external_id(url, job),
            "posted_at": _text(job.get("datePosted")),
        }
        out.update(_salary(job))
        yield {k: v for k, v in out.items() if v is not None and v != ""}
