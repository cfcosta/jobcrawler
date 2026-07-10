
import json
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://programathor.com.br/jobs-rust"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|tr|h[1-6])(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _first_text(value):
    if isinstance(value, str):
        text = unescape(re.sub(r"<[^>]+>", " ", value))
        text = re.sub(r"\s+", " ", text).strip()
        return text or None
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("name", "title", "value"):
            text = _first_text(value.get(key))
            if text:
                return text
    return None


def _jsonld_objects(html):
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    ):
        raw = unescape(match.group(1)).strip()
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)


def _jobposting(html):
    for item in _jsonld_objects(html):
        typ = item.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types):
            return item
    return None


def _location(data):
    if data.get("jobLocationType") == "TELECOMMUTE":
        loc = data.get("jobLocation")
        street = None
        if isinstance(loc, dict):
            addr = loc.get("address")
            if isinstance(addr, dict):
                street = _first_text(addr.get("streetAddress"))
        return street or "Remoto"

    loc = data.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc]
    for entry in locs:
        if isinstance(entry, dict):
            addr = entry.get("address")
            if isinstance(addr, dict):
                pieces = []
                for key in ("addressLocality", "addressRegion", "addressCountry"):
                    text = _first_text(addr.get(key))
                    if text and text not in ("-", "BR"):
                        pieces.append(text)
                if pieces:
                    return ", ".join(pieces)
                text = _first_text(addr.get("streetAddress"))
                if text:
                    return text
        text = _first_text(entry)
        if text:
            return text
    return "Brasil"


def _locations(data, primary):
    values = []
    loc = data.get("jobLocation")
    locs = loc if isinstance(loc, list) else [loc]
    for entry in locs:
        if isinstance(entry, dict):
            addr = entry.get("address")
            if isinstance(addr, dict):
                pieces = []
                for key in ("streetAddress", "addressLocality", "addressRegion", "addressCountry"):
                    text = _first_text(addr.get(key))
                    if text and text not in ("-", ""):
                        pieces.append(text)
                if pieces:
                    values.append(", ".join(pieces))
        else:
            text = _first_text(entry)
            if text:
                values.append(text)
    if not values and primary:
        values.append(primary)

    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _apply_salary(job, data):
    salary = data.get("baseSalary")
    if not isinstance(salary, dict):
        return
    currency = _first_text(salary.get("currency"))
    value = salary.get("value")
    if not isinstance(value, dict):
        return

    unit = _first_text(value.get("unitText"))
    min_value = value.get("minValue")
    max_value = value.get("maxValue")
    fixed = value.get("value")

    if min_value is None and max_value is None and fixed is not None:
        min_value = fixed
        max_value = fixed

    if min_value is not None:
        try:
            job["salary_min"] = float(min_value)
        except Exception:
            pass
    if max_value is not None:
        try:
            job["salary_max"] = float(max_value)
        except Exception:
            pass
    if currency:
        job["salary_currency"] = currency
    if unit:
        job["salary_period"] = unit


def _build_job(url, html):
    data = _jobposting(html)
    if not data:
        return None

    title = _first_text(data.get("title"))
    company = _first_text(data.get("hiringOrganization")) or _first_text(data.get("identifier"))
    description = _html_to_text(data.get("description"))
    location = _location(data)

    if not (title and company and description and location):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    locs = _locations(data, location)
    if locs:
        job["locations"] = locs

    identifier = data.get("identifier")
    if isinstance(identifier, dict):
        external_id = _first_text(identifier.get("value"))
        if external_id:
            job["external_id"] = external_id

    for source_key, job_key in (
        ("datePosted", "posted_at"),
        ("validThrough", "expires_at"),
        ("employmentType", "employment_type"),
        ("url", "apply_url"),
    ):
        value = _first_text(data.get(source_key))
        if value:
            job[job_key] = value

    workplace = _first_text(data.get("jobLocationType"))
    if workplace:
        job["workplace_type"] = workplace

    _apply_salary(job, data)
    return job


def crawl(ctx):
    listing = ctx.fetch(SITE)
    urls = []
    seen = set()
    for href in re.findall(r'href=["\']([^"\']*/jobs/\d+[^"\']*)["\']', listing, re.I):
        url = urljoin(SITE, unescape(href).split("#", 1)[0])
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for url in urls:
        html = ctx.fetch(url)
        job = _build_job(url, html)
        if job:
            yield job
