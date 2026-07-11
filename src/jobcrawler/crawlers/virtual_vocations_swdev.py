
import html
import json
import re

SITE = "https://www.virtualvocations.com/jobs/q-remote+software+developer+jobs/rss"
EXTRA_HOSTS = []

SITEMAP_URL = "https://www.virtualvocations.com/sitemap/index/sitemap.xml"


def _strip_html(value):
    text = value or ""
    text = re.sub(r"(?i)<\s*(br|p|/p|li|/li|ul|/ul|ol|/ol|h[1-6]|/h[1-6]|div|/div|section|/section)\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?s)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _jobposting_from_page(text):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack[:0] = item
            elif isinstance(item, dict):
                typ = item.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack[:0] = graph
    return None


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [v for v in value if v not in (None, "")]
    return [value]


def _organization_name(value):
    if isinstance(value, dict):
        name = value.get("name")
        if name:
            return str(name).strip()
    elif value:
        return str(value).strip()
    return "Virtual Vocations"


def _location_from_ld(data):
    locations = []

    def add(value):
        if value is None or value == "":
            return
        if isinstance(value, list):
            for entry in value:
                add(entry)
            return
        if isinstance(value, dict):
            address = value.get("address")
            if isinstance(address, dict):
                region = address.get("addressRegion")
                country = address.get("addressCountry")
                for r in _as_list(region):
                    if isinstance(r, str) and r.strip():
                        locations.append(r.strip())
                if country and not locations:
                    locations.append(str(country).strip())
            else:
                name = value.get("name")
                if name:
                    locations.append(str(name).strip())
            return
        locations.append(str(value).strip())

    add(data.get("jobLocation"))

    req = data.get("applicantLocationRequirements")
    if not locations and isinstance(req, dict) and req.get("name"):
        locations.append(str(req["name"]).strip())

    if data.get("jobLocationType") == "TELECOMMUTE":
        if not locations:
            locations.append("Remote")
        elif not any(loc.lower() == "remote" for loc in locations):
            locations.insert(0, "Remote")

    deduped = []
    seen = set()
    for loc in locations:
        if loc and loc not in seen:
            deduped.append(loc)
            seen.add(loc)

    if not deduped:
        deduped = ["Remote"]

    return deduped[0], deduped


def _employment_type(value):
    values = [str(v).strip() for v in _as_list(value) if str(v).strip()]
    if not values:
        return None
    return ", ".join(values)


def _workplace_type(value):
    if value == "TELECOMMUTE":
        return "remote"
    if value:
        return str(value)
    return None


def crawl(ctx):
    sitemap = _fetch(ctx, SITEMAP_URL)
    urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sitemap, re.I)

    emitted = 0
    for url in urls:
        url = html.unescape(url.strip())
        if not re.search(r'/job/[^/]*(software|developer)[^/]*-i\.html$', url, re.I):
            continue
        if "/job/" not in url or not url.endswith("-i.html"):
            continue

        page = _fetch(ctx, url)
        data = _jobposting_from_page(page)
        if not data:
            continue

        title = str(data.get("title") or "").strip()
        description = _strip_html(data.get("description") or "")
        if not title or not description:
            continue

        location, locations = _location_from_ld(data)
        job = {
            "url": url,
            "title": title,
            "company": _organization_name(data.get("hiringOrganization")),
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        employment = _employment_type(data.get("employmentType"))
        if employment:
            job["employment_type"] = employment

        workplace = _workplace_type(data.get("jobLocationType"))
        if workplace:
            job["workplace_type"] = workplace

        posted = data.get("datePosted")
        if posted:
            job["posted_at"] = str(posted).strip()

        expires = data.get("validThrough")
        if expires:
            job["expires_at"] = str(expires).strip()

        match = re.search(r"-(\d+)-i\.html$", url)
        if match:
            job["external_id"] = match.group(1)

        yield job
        emitted += 1
        if emitted >= 25:
            return
