import html
import json
import re
from urllib.parse import urljoin

# Maxio runs a JazzHR (Resumator) board. There is no JSON feed
# (``?format=json`` just returns the HTML board), so we scrape the board's
# job table for the (title, location, hash) triples and then fetch each
# ``/apply/jobs/details/<hash>`` page, whose ``application/ld+json``
# JobPosting block carries the canonical url, full description, datePosted
# and employmentType.
SITE = "https://maxio.applytojob.com/apply/jobs/"
EXTRA_HOSTS = []
COMPANY = "Maxio"
MAX_JOBS = 100


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?is)</\s*(p|div|li|h1|h2|h3|h4|ul|ol|tr)\s*>", "\n", text)
    text = re.sub(r"(?is)<\s*(p|div|li|h1|h2|h3|h4|ul|ol|tr)[^>]*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _clean_line(value):
    """Collapse a snippet to a single trimmed line (for titles / locations)."""
    text = _strip_html(value)
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def _iter_jsonld(page):
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    ):
        try:
            data = json.loads(html.unescape(raw).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)


def _job_posting(page):
    for obj in _iter_jsonld(page):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
    return None


def _ld_location(job):
    loc = job.get("jobLocation")
    if isinstance(loc, dict):
        loc = [loc]
    if not isinstance(loc, list):
        return None
    parts = []
    for entry in loc:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if not isinstance(address, dict):
            continue
        vals = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            val = _clean_line(address.get(key))
            if val and val not in vals:
                vals.append(val)
        if vals:
            joined = ", ".join(vals)
            if joined not in parts:
                parts.append(joined)
    return "; ".join(parts) if parts else None


def _workplace_type(location):
    low = (location or "").lower()
    if "remote" in low:
        return "Remote"
    if "hybrid" in low:
        return "Hybrid"
    return None


def _external_id(job):
    code = job.get("uniqueJobCode")
    ident = _clean_line(code) if code else None
    if ident:
        return ident
    identifier = job.get("identifier")
    if isinstance(identifier, dict):
        return _clean_line(identifier.get("value") or identifier.get("name"))
    return _clean_line(identifier)


# Desktop table row: title anchor + department span in the first cell, plain
# location text in the following cell.
_ROW_RE = re.compile(
    r'<a[^>]*class="job_title_link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<span[^>]*class="resumator_department"[^>]*>(.*?)</span>'
    r"\s*</td>\s*<td[^>]*>(.*?)</td>",
    re.I | re.S,
)
# Fallback: any job link (used only if the table shape changes).
_LINK_RE = re.compile(
    r'<a[^>]*class="job_title_link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_HASH_RE = re.compile(r"/apply/jobs/details/([A-Za-z0-9]+)")


def _board_rows(page):
    """Yield (detail_url, hash, title, department, location) for each job.

    Prefers the desktop table (title+location together); falls back to bare
    links (location then comes from the detail page's ld+json). Dedups the
    desktop/mobile duplicate rows by hash.
    """
    seen = set()
    rows = list(_ROW_RE.finditer(page))
    if rows:
        for m in rows:
            href, title, dept, location = m.groups()
            hm = _HASH_RE.search(href)
            if not hm or hm.group(1) in seen:
                continue
            seen.add(hm.group(1))
            yield (
                urljoin(SITE, html.unescape(href.strip())),
                hm.group(1),
                _clean_line(title),
                _clean_line(dept),
                _clean_line(location),
            )
        return
    for m in _LINK_RE.finditer(page):
        href, title = m.groups()
        hm = _HASH_RE.search(href)
        if not hm or hm.group(1) in seen:
            continue
        seen.add(hm.group(1))
        yield (
            urljoin(SITE, html.unescape(href.strip())),
            hm.group(1),
            _clean_line(title),
            None,
            None,
        )


def crawl(ctx):
    board = ctx.fetch(SITE)
    yielded = 0

    for detail_url, job_hash, list_title, department, list_location in _board_rows(
        board
    ):
        if yielded >= MAX_JOBS:
            break

        page = ctx.fetch(detail_url)
        data = _job_posting(page) or {}

        title = _clean_line(data.get("title")) or list_title
        description = _strip_html(data.get("description"))
        location = list_location or _ld_location(data)
        url = _clean_line(data.get("url")) or detail_url

        if not (title and url and description):
            continue
        if not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "apply_url": url,
            "external_id": _external_id(data) or job_hash,
            "department": department,
            "employment_type": _clean_line(data.get("employmentType")),
            "workplace_type": _workplace_type(location),
            "posted_at": _clean_line(data.get("datePosted")),
            "expires_at": _clean_line(data.get("validThrough")),
        }
        for key, value in optional.items():
            if value:
                job[key] = value

        if location:
            job["locations"] = [location]

        yielded += 1
        yield job
