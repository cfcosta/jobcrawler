from __future__ import annotations

import base64
import html
import json
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

SITE = "https://web3.career/"
EXTRA_HOSTS = []

SOURCE = "web3career"
LISTINGS = (SITE, urljoin(SITE, "?page=2"))

# Apply capture (#130): each row's Apply CTA is an on-site tracking chain, and
# resolving it to the real ATS posting costs 2 fetches per job — too much to
# spend on every one of ~106 rows inside the ~200-page crawl budget. Each crawl
# resolves a day-rotated window of this many rows; the runner preserves links
# captured on earlier crawls for rows outside today's window (apply_url is
# omit-when-absent), so coverage accrues across days.
_APPLY_BUDGET = 60

# The interstitial hides its destination in
# ``VAR = "<blob>".slice(a, b).split('').reverse().join('')`` — undoing it is
# pure string work, no JS execution.
_APPLY_DEST_RE = re.compile(
    r'=\s*"((?:[^"\\]|\\.)+)"'
    r"\.slice\(\s*(\d+)\s*,\s*(\d+)\s*\)"
    r"\.split\(['\"]{2}\)\.reverse\(\)\.join\(['\"]{2}\)"
)
_JS_ESCAPE_RE = re.compile(r"\\(.)")

_ROW_RE = re.compile(r"tableTurboRowClick\(event,\s*'(/([^'/]+)/(\d+))'")
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.I)
_BLOCK_RE = re.compile(r"</\s*(?:p|div|li|h[1-6]|tr|ul|ol|section)\s*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTINL_RE = re.compile(r"\n\s*\n\s*")
_TRIMNL_RE = re.compile(r"[ \t]*\n[ \t]*")

_DATE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*([+-]\d{2}:?\d{2}|Z)?"
)
_DATEONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLACEHOLDER_LOC = {"anywhere", "n/a", "worldwide", "global"}
_PERIOD_MAP = {
    "YEAR": "year",
    "YEARLY": "year",
    "ANNUAL": "year",
    "MONTH": "month",
    "MONTHLY": "month",
    "HOUR": "hour",
    "HOURLY": "hour",
}


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = _BR_RE.sub("\n", value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _WS_RE.sub(" ", value)
    value = _MULTINL_RE.sub("\n\n", value)
    value = _TRIMNL_RE.sub("\n", value)
    return value.strip() or None


def _slugify(value):
    return _SLUG_RE.sub("-", html.unescape(str(value or "")).lower()).strip("-")


def _iso(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if _DATEONLY_RE.match(text):
        return text + "T00:00:00"
    match = _DATE_RE.search(text)
    if not match:
        return None
    stamp = f"{match.group(1)}T{match.group(2)}"
    tz = match.group(3)
    if not tz:
        return stamp
    if tz == "Z":
        return stamp + "+00:00"
    if ":" not in tz:
        tz = tz[:3] + ":" + tz[3:]
    return stamp + tz


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _find_jobposting(obj):
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            typ = item.get("@type")
            if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                return item
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            for val in item.values():
                if isinstance(val, (dict, list)):
                    stack.append(val)
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _jobpostings(page):
    out = []
    for raw in _LDJSON_RE.findall(page):
        try:
            data = json.loads(html.unescape(raw.strip()))
        except ValueError:
            continue
        job = _find_jobposting(data)
        if not isinstance(job, dict):
            continue
        title_slug = _slugify(job.get("title"))
        if not title_slug:
            continue
        out.append((title_slug, _org_name(job), job))
    return out


def _rows(page):
    seen = set()
    out = []
    for match in _ROW_RE.finditer(page):
        path, slug, ident = match.group(1), match.group(2), match.group(3)
        if path in seen:
            continue
        seen.add(path)
        out.append((path, slug, ident))
    return out


def _company_tokens(company):
    slug = _slugify(company)
    tokens = set()
    if slug:
        tokens.add(slug)
        tokens.add(slug.replace("-", ""))
    return {t for t in tokens if t}


def _match_block(row_slug, blocks):
    candidates = [
        block
        for block in blocks
        if row_slug == block[0] or row_slug.startswith(block[0] + "-")
    ]
    if not candidates:
        return None
    longest = max(len(block[0]) for block in candidates)
    best = [block for block in candidates if len(block[0]) == longest]
    if len(best) == 1:
        return best[0][2]
    suffix = row_slug[longest + 1 :]
    condensed = suffix.replace("-", "")
    for _slug, company, job in best:
        for token in _company_tokens(company):
            if token in suffix or token in condensed or condensed in token:
                return job
    return None


def _org_name(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return _text(org.get("name"))
    return _text(org)


def _address_parts(addr):
    if isinstance(addr, str):
        piece = addr.strip()
        return [piece] if piece else []
    if not isinstance(addr, dict):
        return []
    bits = [
        addr.get("addressLocality"),
        addr.get("addressRegion"),
        addr.get("addressCountry"),
    ]
    cleaned = []
    for bit in bits:
        if not bit:
            continue
        piece = str(bit).strip()
        if not piece or piece.lower() in _PLACEHOLDER_LOC:
            continue
        if piece not in cleaned:
            cleaned.append(piece)
    return cleaned


def _location(job):
    location = job.get("jobLocation")
    if isinstance(location, list):
        entries = location
    elif location:
        entries = [location]
    else:
        entries = []
    parts = []
    for entry in entries:
        addr = entry.get("address", entry) if isinstance(entry, dict) else entry
        joined = ", ".join(_address_parts(addr))
        if joined and joined not in parts:
            parts.append(joined)
    if not parts:
        req = job.get("applicantLocationRequirements")
        names = []
        if isinstance(req, dict):
            names = [req.get("name")]
        elif isinstance(req, list):
            names = [r.get("name") for r in req if isinstance(r, dict)]
        for name in names:
            if name and str(name).strip().lower() not in _PLACEHOLDER_LOC:
                parts.append(str(name).strip())
    if not parts and str(job.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        parts.append("Remote")
    return "; ".join(parts) if parts else None


def _salary_struct(job):
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return {}
    value = base.get("value")
    out = {}
    if isinstance(value, dict):
        minv = _num(value.get("minValue"))
        maxv = _num(value.get("maxValue"))
        single = _num(value.get("value"))
        lo = minv if minv is not None else single
        hi = maxv if maxv is not None else single
        if lo is not None:
            out["salary_min"] = lo
        if hi is not None:
            out["salary_max"] = hi
        period = _PERIOD_MAP.get(str(value.get("unitText") or "").strip().upper())
        if period:
            out["salary_period"] = period
    else:
        single = _num(value)
        if single is not None:
            out["salary_min"] = single
            out["salary_max"] = single
    if "salary_min" in out or "salary_max" in out:
        currency = (
            str(base.get("currency") or base.get("currencyCode") or "").strip().upper()
        )
        if currency:
            out["salary_currency"] = currency
    return out


def _build(job, url, ident):
    title = _text(job.get("title"))
    company = _org_name(job)
    description = _text(job.get("description"))
    location = _location(job)
    if not (title and company and location and description):
        return None
    record = {
        "source": SOURCE,
        "url": url,
        "external_id": ident,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
    }
    employment = job.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(x) for x in employment if x)
    employment = _text(employment)
    if employment:
        record["employment_type"] = employment
    workplace = _text(job.get("jobLocationType"))
    if workplace:
        record["workplace_type"] = workplace
    posted = _iso(job.get("datePosted"))
    if posted:
        record["posted_at"] = posted
    expires = _iso(job.get("validThrough"))
    if expires:
        record["expires_at"] = expires
    record.update(_salary_struct(job))
    return record


def _interstitial_path(ident):
    """The on-site tracking hop behind a row's Apply CTA: /i/<b64(ident) reversed>.

    The job page's own "Apply Now" anchor points at ``/i/<token>`` where the
    token is exactly ``base64(str(ident))`` written backwards (ident 96380 →
    ``OTYzODA=`` → ``=ADOzYTO``) — derivable from the listing row for free, so
    the job page itself never needs fetching.
    """
    return "/i/" + base64.b64encode(str(ident).encode()).decode()[::-1]


def _apply_url(ctx, job_url, ident):
    """Resolve a row's Apply chain (/i/ → obfuscated /z/ → 302) to the real ATS URL.

    Both tracking hops refuse referer-less requests (bot defense) — each must
    carry the page that links it. The /z/ hash is salted per request, so the
    interstitial fetch cannot be skipped. The final URL is the /z/ 302's
    Location, captured by ``ctx.resolve`` without fetching the foreign ATS
    page; a same-site or non-http result (the bounce-back defense tripping)
    is reported as no capture.
    """
    i_url = urljoin(SITE, _interstitial_path(ident))
    page = ctx.fetch(i_url, headers={"Referer": job_url})
    match = _APPLY_DEST_RE.search(page)
    if not match:
        return None
    blob = _JS_ESCAPE_RE.sub(r"\1", match.group(1))
    dest = blob[int(match.group(2)) : int(match.group(3))][::-1]
    if not dest:
        return None
    final = ctx.resolve(urljoin(SITE, dest), headers={"Referer": i_url})
    if not final.startswith(("http://", "https://")):
        return None
    host = urlsplit(final).hostname or ""
    if host == "web3.career" or host.endswith(".web3.career"):
        return None
    return final


def _apply_window(count, budget, seed):
    """Which record ordinals (0-based) spend the 2 apply fetches this crawl.

    A circular window of ``budget`` ordinals starting at ``(seed * budget) %
    count``: consecutive seeds (days) tile the whole list, so every row gets
    resolved within ``ceil(count / budget)`` crawl-days while no single crawl
    exceeds its share of the page budget.
    """
    if count <= 0:
        return set()
    if count <= budget:
        return set(range(count))
    start = (seed * budget) % count
    return {(start + i) % count for i in range(budget)}


def crawl(ctx):
    seen_urls = set()
    records = []
    for listing in LISTINGS:
        try:
            page = ctx.fetch(listing)
        except Exception:
            continue
        blocks = _jobpostings(page)
        if not blocks:
            continue
        for path, slug, ident in _rows(page):
            url = urljoin(SITE, path)
            if url in seen_urls:
                continue
            job = _match_block(slug, blocks)
            if job is None:
                continue
            record = _build(job, url, ident)
            if record is None:
                continue
            seen_urls.add(url)
            records.append(record)
    window = _apply_window(len(records), _APPLY_BUDGET, date.today().toordinal())
    for index, record in enumerate(records):
        if index in window:
            try:
                apply_url = _apply_url(ctx, record["url"], record["external_id"])
            except Exception:
                apply_url = None  # fail-open: the record ships without a capture
            if apply_url:
                record["apply_url"] = apply_url
        yield record
