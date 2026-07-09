import json
import re
import html
from xml.etree import ElementTree as ET

SITE = "https://jobsboard.zeroknowledge.fm/jobs/"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h1|h2|h3|h4|tr|table|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</\s*li\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*td\s*>\s*<\s*td[^>]*>", " ", text)
    text = re.sub(r"(?is)<\s*(script|style)[^>]*>.*?</\s*\1\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_jobposting_ld(page):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    )
    for script in scripts:
        raw = html.unescape(script.strip())
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            typ = obj.get("@type")
            if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                return obj
            graph = obj.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return None


def _as_text(value):
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value) if value is not None else ""


def _location_text(value):
    locations = value if isinstance(value, list) else [value]
    out = []
    for loc in locations:
        if isinstance(loc, str):
            out.append(loc)
            continue
        if not isinstance(loc, dict):
            continue
        address = loc.get("address")
        pieces = []
        if isinstance(address, dict):
            for key in (
                "streetAddress",
                "addressLocality",
                "addressRegion",
                "postalCode",
                "addressCountry",
            ):
                part = address.get(key)
                if isinstance(part, dict):
                    part = part.get("name")
                if part:
                    pieces.append(str(part))
        elif address:
            pieces.append(str(address))
        if loc.get("name"):
            pieces.insert(0, str(loc.get("name")))
        if pieces:
            out.append(", ".join(pieces))
    seen = []
    for item in out:
        if item and item not in seen:
            seen.append(item)
    return "; ".join(seen)


def _salary(data):
    salary = data.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    value = salary.get("value")
    currency = salary.get("currency")
    unit = None
    minimum = maximum = raw = None
    if isinstance(value, dict):
        unit = value.get("unitText")
        minimum = value.get("minValue")
        maximum = value.get("maxValue")
        raw = value.get("value")
    else:
        raw = value
    pieces = []
    if currency:
        pieces.append(str(currency))
    if minimum is not None and maximum is not None:
        pieces.append("%s-%s" % (minimum, maximum))
    elif minimum is not None:
        pieces.append("from %s" % minimum)
    elif maximum is not None:
        pieces.append("up to %s" % maximum)
    elif raw is not None:
        pieces.append(str(raw))
    if unit:
        pieces.append(str(unit))
    return " ".join(pieces).strip() or None


def _feed_links(ctx):
    text = ctx.fetch("https://jobsboard.zeroknowledge.fm/rss/")
    links = []
    try:
        root = ET.fromstring(text)
        for item in root.findall(".//item"):
            link = item.findtext("link")
            if link:
                links.append(link.strip())
    except Exception:
        links.extend(
            re.findall(
                r"<link>\s*(https://jobsboard\.zeroknowledge\.fm/job/\d+/[^<]+)\s*</link>",
                text,
                re.I,
            )
        )
    seen = set()
    for link in links:
        link = html.unescape(link)
        if "/job/" in link and link not in seen:
            seen.add(link)
            yield link


def crawl(ctx):
    for url in _feed_links(ctx):
        page = ctx.fetch(url)
        data = _find_jobposting_ld(page)
        if not data:
            continue

        title = _as_text(data.get("title")).strip()
        hiring_org = data.get("hiringOrganization")
        company = _as_text(
            hiring_org.get("name") if isinstance(hiring_org, dict) else hiring_org
        ).strip()
        location = (
            _location_text(data.get("jobLocation"))
            or _as_text(data.get("applicantLocationRequirements")).strip()
        )
        description = _html_to_text(data.get("description") or "")
        job_url = _as_text(data.get("url")).strip() or url

        job = {
            "url": job_url,
            "title": title,
            "company": company,
            "location": location or "Remote",
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": _as_text(data.get("employmentType")).strip(),
            "workplace_type": _as_text(data.get("jobLocationType")).strip(),
            "department": _as_text(
                data.get("industry") or data.get("occupationalCategory")
            ).strip(),
            "posted_at": _as_text(data.get("datePosted")).strip(),
            "expires_at": _as_text(data.get("validThrough")).strip(),
            "external_id": _as_text(data.get("identifier")).strip(),
            "apply_url": job_url,
            "salary": _salary(data),
        }
        for key, value in optional.items():
            if value:
                job[key] = value

        yield job
