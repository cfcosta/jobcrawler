
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://nodesk.co/remote-jobs/development/"
EXTRA_HOSTS = []

SITEMAP_URL = "https://nodesk.co/sitemap-jobs.xml"


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _location_from_jsonld(data):
    parts = []
    for loc in _as_list(data.get("applicantLocationRequirements")):
        if isinstance(loc, dict):
            name = loc.get("name")
            if name:
                parts.append(str(name))
        elif loc:
            parts.append(str(loc))
    if not parts:
        for loc in _as_list(data.get("jobLocation")):
            if isinstance(loc, dict):
                address = loc.get("address")
                if isinstance(address, dict):
                    bits = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
                    text = ", ".join(str(x) for x in bits if x)
                    if text:
                        parts.append(text)
                elif address:
                    parts.append(str(address))
                elif loc.get("name"):
                    parts.append(str(loc.get("name")))
            elif loc:
                parts.append(str(loc))
    if data.get("jobLocationType") == "TELECOMMUTE":
        if parts:
            return "Remote - " + ", ".join(dict.fromkeys(parts))
        return "Remote"
    return ", ".join(dict.fromkeys(parts)) or None


def _salary_from_jsonld(data):
    salary = data.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    currency = salary.get("currency")
    value = salary.get("value")
    if isinstance(value, dict):
        min_v = value.get("minValue") or value.get("value")
        max_v = value.get("maxValue")
        unit = value.get("unitText")
        bits = []
        if min_v is not None and max_v is not None and min_v != max_v:
            bits.append("%s-%s" % (min_v, max_v))
        elif min_v is not None:
            bits.append(str(min_v))
        if currency:
            bits.append(str(currency))
        if unit:
            bits.append(str(unit))
        return " ".join(bits) or None
    if value is not None:
        return "%s %s" % (value, currency) if currency else str(value)
    return None


def _jsonld_job(page):
    for match in re.finditer(r"<script[^>]*type\s*=\s*(?:\"application/ld\+json\"|'application/ld\+json'|application/ld\+json)[^>]*>(.*?)</script>", page, re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack[:0] = item
            elif isinstance(item, dict):
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack[:0] = graph
                typ = item.get("@type")
                types = typ if isinstance(typ, list) else [typ]
                if "JobPosting" in types:
                    return item
    return None


def _apply_url(page):
    match = re.search(r"\bdata-apply-url\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^>\s]+))", page, re.I)
    if match:
        return html.unescape(next(g for g in match.groups() if g)).strip() or None
    return None


def _make_job(url, page, data):
    org = data.get("hiringOrganization")
    company = None
    if isinstance(org, dict):
        company = org.get("name")
    elif org:
        company = str(org)

    title = data.get("title")
    description = _text_from_html(data.get("description"))
    location = _location_from_jsonld(data)
    if not (title and company and location and description):
        return None

    kwargs = {
        "url": url,
        "title": str(title).strip(),
        "company": str(company).strip(),
        "location": str(location).strip(),
        "description": description,
        "source": SITE,
    }

    posted_at = data.get("datePosted")
    if posted_at:
        kwargs["posted_at"] = str(posted_at)
    expires_at = data.get("validThrough")
    if expires_at:
        kwargs["expires_at"] = str(expires_at)

    employment = data.get("employmentType")
    if employment:
        kwargs["employment_type"] = ", ".join(str(x) for x in _as_list(employment) if x)

    if data.get("jobLocationType"):
        kwargs["workplace_type"] = str(data.get("jobLocationType"))

    salary = _salary_from_jsonld(data)
    if salary:
        kwargs["salary"] = salary

    apply_url = _apply_url(page)
    if apply_url:
        kwargs["apply_url"] = apply_url

    return kwargs


def crawl(ctx):
    sitemap = _fetch(ctx, SITEMAP_URL)
    urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sitemap)
    seen = set()
    fetched = 0
    max_pages = 25
    for url in urls:
        url = html.unescape(url).strip()
        if not url.startswith("https://nodesk.co/remote-jobs/") or url in seen:
            continue
        seen.add(url)
        if fetched >= max_pages:
            break
        fetched += 1
        page = _fetch(ctx, url)
        data = _jsonld_job(page)
        if not data:
            continue
        job = _make_job(url, page, data)
        if job:
            yield job
