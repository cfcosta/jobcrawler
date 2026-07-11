
import json
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://remoteleaf.com/jobs/software-engineer/"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|ul|ol|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _json_ld_objects(html):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                elif isinstance(graph, dict):
                    stack.append(graph)
            elif isinstance(item, list):
                stack.extend(item)


def _first_text(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("value")
    if value is None:
        return None
    return str(value)


def _locations(data):
    reqs = data.get("applicantLocationRequirements")
    if isinstance(reqs, dict):
        reqs = [reqs]
    names = []
    if isinstance(reqs, list):
        for req in reqs:
            name = _first_text(req)
            if name and name not in names:
                names.append(name)
    loc = data.get("jobLocation")
    if loc and not names:
        for item in (loc if isinstance(loc, list) else [loc]):
            if isinstance(item, dict) and item.get("address"):
                name = _first_text(item.get("address"))
            else:
                name = _first_text(item)
            if name and name not in names:
                names.append(name)
    if not names and data.get("jobLocationType"):
        names.append(str(data.get("jobLocationType")))
    return names


def _detail_links(html):
    seen = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        url = urljoin(SITE, href)
        if re.match(r"https://remoteleaf\.com/company/[^/]+/[^/]+/$", url) and url not in seen:
            seen.add(url)
            yield url


def _job(url, data):
    org = data.get("hiringOrganization") or {}
    ident = data.get("identifier") or {}
    locations = _locations(data)
    out = {
        "url": url,
        "title": data.get("title"),
        "company": org.get("name") or ident.get("name"),
        "location": locations[0] if locations else None,
        "description": _strip_html(data.get("description")),
        "source": SITE,
    }
    if len(locations) > 1:
        out["locations"] = locations
    for src, dst in (
        ("employmentType", "employment_type"),
        ("jobLocationType", "workplace_type"),
        ("datePosted", "posted_at"),
        ("validThrough", "expires_at"),
    ):
        if data.get(src):
            out[dst] = data.get(src)
    if isinstance(ident, dict) and ident.get("value"):
        out["external_id"] = ident.get("value")
    salary = data.get("baseSalary")
    if isinstance(salary, dict):
        if salary.get("currency"):
            out["salary_currency"] = salary.get("currency")
        value = salary.get("value")
        if isinstance(value, dict):
            if value.get("minValue") is not None:
                out["salary_min"] = value.get("minValue")
            if value.get("maxValue") is not None:
                out["salary_max"] = value.get("maxValue")
            if value.get("unitText"):
                out["salary_period"] = value.get("unitText")
    return {k: v for k, v in out.items() if v not in (None, "")}


def crawl(ctx):
    seen = set()
    for page in range(1, 3):
        listing_url = SITE if page == 1 else SITE.rstrip("/") + "/?page=" + str(page)
        listing = ctx.fetch(listing_url)
        for url in _detail_links(listing):
            if url in seen:
                continue
            seen.add(url)
            html = ctx.fetch(url)
            for obj in _json_ld_objects(html):
                if obj.get("@type") == "JobPosting":
                    job = _job(url, obj)
                    if job.get("title") and job.get("company") and job.get("location") and job.get("description"):
                        yield job
                    break
