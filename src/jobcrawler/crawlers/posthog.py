import html
import json
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

SITE = "https://posthog.com/careers"
EXTRA_HOSTS = []

SOURCE = "posthog"
COMPANY = "PostHog"

SITEMAP_INDEX = "/sitemap/sitemap-index.xml"
CAREERS_PREFIX = "/careers/"
MAX_SHARDS = 8
MAX_ROLES = 50

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK = re.compile(
    r"(?i)</\s*(p|div|li|h[1-6]|section|tr|ul|ol|details|summary|table)\s*>"
)
_WS = re.compile(r"[ \t\r\f\v]+")
_LEAD = re.compile(r"\n[ \t]+")
_GAP = re.compile(r"\n{3,}")


def _clean_text(value):
    if not isinstance(value, str) or not value:
        return None
    text = html.unescape(value)
    text = _BR.sub("\n", text)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = _LEAD.sub("\n", text)
    text = _GAP.sub("\n\n", text)
    return text.strip() or None


def _locs(xml):
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []
    out = []
    for el in root.findall(".//{*}loc"):
        if el.text and el.text.strip():
            out.append(el.text.strip())
    return out


def _careers_candidates(ctx):
    try:
        index = ctx.fetch(urljoin(SITE, SITEMAP_INDEX))
    except Exception:
        return []
    candidates = []
    for shard in _locs(index)[:MAX_SHARDS]:
        try:
            shard_xml = ctx.fetch(shard)
        except Exception:
            continue
        for loc in _locs(shard_xml):
            path = urlparse(loc).path
            if path.startswith(CAREERS_PREFIX) and len(path) > len(CAREERS_PREFIX):
                candidates.append(path)
        if candidates:
            break
    return list(dict.fromkeys(candidates))


def _page_data(ctx, path):
    url = urljoin(SITE, "/page-data" + path + "/page-data.json")
    try:
        raw = ctx.fetch(url)
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _data(page_data):
    result = (page_data or {}).get("result") or {}
    return result.get("data") or {}


def _open_paths(data):
    nodes = ((data.get("allJobPostings") or {}).get("nodes")) or []
    paths = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        slug = (node.get("fields") or {}).get("slug")
        if slug:
            paths.append(slug)
    return list(dict.fromkeys(paths))


def _custom_fields(posting):
    out = {}
    for field in (posting.get("parent") or {}).get("customFields") or []:
        if isinstance(field, dict) and field.get("title"):
            out[field["title"]] = field.get("value")
    return out


# PostHog states the hiring region as "<Region> (GMT band)". A global region word
# qualified by a bounded timezone band ("Anywhere (GMT +2 to GMT -8)") is not
# worldwide — the band excludes Asia/Pacific — so demote the leading global token
# to "Remote", leaving the band for context. Real regions ("Americas (GMT -3 to
# GMT -8)", "UK (GMT 0)") start with a place word and are left untouched.
_GLOBAL_TZ_RE = re.compile(
    r"^\s*(?:anywhere|worldwide|global|international)\b"
    r"(?=.*(?:gmt|utc)\s*[-+−]?\s*\d)",
    re.I,
)


def _demote_global_tz_band(value):
    if not value:
        return value
    return _GLOBAL_TZ_RE.sub("Remote", value, count=1)


def _location(posting):
    fields = _custom_fields(posting)
    for key in ("Timezone", "Timezone(s)"):
        raw = fields.get(key)
        if not isinstance(raw, str):
            continue
        raw = raw.strip()
        if not raw:
            continue
        if raw[:1] == "[":
            try:
                values = json.loads(raw)
            except Exception:
                values = None
            if isinstance(values, list):
                parts = [
                    _demote_global_tz_band(str(v).strip())
                    for v in values
                    if str(v).strip()
                ]
                if parts:
                    return "; ".join(parts)
        cleaned = _clean_text(raw)
        if cleaned:
            return _demote_global_tz_band(cleaned)
    return "Remote"


def _build_job(data, path):
    posting = data.get("ashbyJobPosting")
    if not isinstance(posting, dict):
        return None
    fields = posting.get("fields") or {}
    info = posting.get("info") or {}
    title = _clean_text(fields.get("title"))
    description = _clean_text(info.get("descriptionHtml")) or _clean_text(
        fields.get("html")
    )
    location = _location(posting)
    slug = fields.get("slug") or path
    url = urljoin(SITE, slug)
    if not (title and description and location and url):
        return None
    job = {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": location,
        "description": description,
        "source": SOURCE,
        "workplace_type": "Remote",
    }
    ext = posting.get("id")
    if ext:
        job["external_id"] = str(ext)
    department = _clean_text(posting.get("departmentName"))
    if department:
        job["department"] = department
    return job


def crawl(ctx):
    candidates = _careers_candidates(ctx)
    if not candidates:
        return

    cache = {}
    open_paths = None
    for path in candidates:
        page_data = _page_data(ctx, path)
        cache[path] = page_data
        found = _open_paths(_data(page_data))
        if found:
            open_paths = found
            break
    if open_paths is None:
        open_paths = candidates

    seen = set()
    count = 0
    for path in open_paths:
        if count >= MAX_ROLES:
            break
        page_data = cache.get(path)
        if page_data is None:
            page_data = _page_data(ctx, path)
        job = _build_job(_data(page_data), path)
        if not job or job["url"] in seen:
            continue
        seen.add(job["url"])
        count += 1
        yield job
