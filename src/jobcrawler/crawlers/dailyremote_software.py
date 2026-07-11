
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://dailyremote.com/remote-software-development-jobs"
EXTRA_HOSTS = []


def _strip_tags(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|ol|ul|h[1-6]|section|article|tr)>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _ld_blocks(doc):
    return re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        doc or "",
        re.I | re.S,
    )


def _json_ld_objects(doc):
    for block in _ld_blocks(doc):
        text = html.unescape(block).strip()
        try:
            yield json.loads(text)
        except Exception:
            try:
                yield json.loads(block.strip())
            except Exception:
                continue


def _graph_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _graph_nodes(item)
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for node in data["@graph"]:
                if isinstance(node, dict):
                    yield node
        else:
            yield data


def _listing_items(doc):
    for data in _json_ld_objects(doc):
        for node in _graph_nodes(data):
            typ = node.get("@type")
            if typ == "ItemList" or (isinstance(typ, list) and "ItemList" in typ):
                for item in node.get("itemListElement") or []:
                    if isinstance(item, dict):
                        url = item.get("url") or (item.get("item") or {}).get("@id")
                        name = item.get("name")
                        if url:
                            yield urljoin(SITE, url), name


def _scan_json_string(doc, key):
    # Detail pages contain a malformed JobPosting block; scan quoted string values tolerantly.
    marker = '"' + key + '"'
    idx = doc.find(marker)
    if idx < 0:
        return None
    colon = doc.find(":", idx + len(marker))
    if colon < 0:
        return None
    pos = colon + 1
    while pos < len(doc) and doc[pos].isspace():
        pos += 1
    if pos >= len(doc) or doc[pos] != '"':
        return None
    pos += 1
    out = []
    escaped = False
    while pos < len(doc):
        ch = doc[pos]
        if escaped:
            if ch == "n":
                out.append("\n")
            elif ch == "r":
                out.append("\r")
            elif ch == "t":
                out.append("\t")
            elif ch == "u" and pos + 4 < len(doc):
                hexdigits = doc[pos + 1:pos + 5]
                try:
                    out.append(chr(int(hexdigits, 16)))
                    pos += 4
                except Exception:
                    out.append(ch)
            else:
                out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            return "".join(out)
        else:
            out.append(ch)
        pos += 1
    return None


def _first_jobposting(doc):
    for data in _json_ld_objects(doc):
        for node in _graph_nodes(data):
            typ = node.get("@type")
            if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                return node
    return None


def _company_from(value):
    if isinstance(value, dict):
        return value.get("name")
    return value if isinstance(value, str) else None


def _title_company_from_listing(name):
    if not name:
        return None, None
    parts = re.split(r"\s+at\s+", name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    return name.strip() or None, None


def _extract_detail(doc, url, listing_name=None):
    node = _first_jobposting(doc) or {}
    listing_title, listing_company = _title_company_from_listing(listing_name)

    title = node.get("title") or _scan_json_string(doc, "title") or listing_title
    company = _company_from(node.get("hiringOrganization")) or _scan_json_string(doc, "name") or listing_company
    if not company or company == "[Unlock with Premium]":
        company = listing_company or "DailyRemote"

    raw_desc = node.get("description") or _scan_json_string(doc, "description")
    description = _strip_tags(raw_desc)
    if not description:
        og = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', doc, re.I | re.S)
        description = _strip_tags(og.group(1)) if og else None

    location = None
    loc_reqs = node.get("applicantLocationRequirements")
    if isinstance(loc_reqs, dict):
        location = loc_reqs.get("name")
    elif isinstance(loc_reqs, list):
        names = [x.get("name") for x in loc_reqs if isinstance(x, dict) and x.get("name")]
        location = ", ".join(names) if names else None
    if not location and (node.get("jobLocationType") or _scan_json_string(doc, "jobLocationType")) == "TELECOMMUTE":
        location = "Remote"
    location = location or "Remote"

    job = {
        "url": node.get("url") or _scan_json_string(doc, "url") or url,
        "title": _strip_tags(title),
        "company": _strip_tags(company),
        "location": _strip_tags(location),
        "description": description,
        "source": SITE,
    }

    field_map = [
        ("employmentType", "employment_type"),
        ("jobLocationType", "workplace_type"),
        ("datePosted", "posted_at"),
        ("validThrough", "expires_at"),
    ]
    for src, dst in field_map:
        value = node.get(src) or _scan_json_string(doc, src)
        value = _strip_tags(value)
        if value:
            job[dst] = value

    ident = node.get("identifier")
    if isinstance(ident, dict):
        ext = ident.get("value")
    else:
        ext = ident
    ext = _strip_tags(ext or _scan_json_string(doc, "value"))
    if ext:
        job["external_id"] = ext

    return {k: v for k, v in job.items() if v is not None and v != ""}


def crawl(ctx):
    seen = set()
    page = 1
    yielded = 0
    max_pages = 2
    max_jobs = 40
    while page <= max_pages and yielded < max_jobs:
        list_url = SITE if page == 1 else SITE + "?page=" + str(page)
        doc = ctx.fetch(list_url)
        items = list(_listing_items(doc))
        if not items:
            break

        new_on_page = 0
        for job_url, listing_name in items:
            if job_url in seen:
                continue
            seen.add(job_url)
            new_on_page += 1
            detail = ctx.fetch(job_url)
            job = _extract_detail(detail, job_url, listing_name)
            if job.get("title") and job.get("description"):
                yielded += 1
                yield job
                if yielded >= max_jobs:
                    break

        if new_on_page == 0 or len(items) < 20:
            break
        page += 1
