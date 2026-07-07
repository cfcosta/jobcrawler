import html
import json
import re
from urllib.parse import urljoin

SITE = "https://web3.career/"
EXTRA_HOSTS = []

_LISTINGS = (SITE, SITE + "?page=2")
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_DETAIL_RE = re.compile(r'href="(/[a-z0-9]+(?:-[a-z0-9]+)+/([0-9]+))"')
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DATE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*([+-]\d{2}:?\d{2}|Z)?"
)


def _text(value):
    if not isinstance(value, str):
        return None
    value = html.unescape(value)
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
    value = re.sub(r"</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _slugify(value):
    return _SLUG_RE.sub("-", html.unescape(str(value)).lower()).strip("-")


def _iso(value):
    if not isinstance(value, str):
        return None
    match = _DATE_RE.search(value.strip())
    if not match:
        return None
    date, time, tz = match.group(1), match.group(2), match.group(3)
    stamp = f"{date}T{time}"
    if tz == "Z":
        return stamp + "+00:00"
    if tz:
        if ":" not in tz:
            tz = tz[:3] + ":" + tz[3:]
        return stamp + tz
    return stamp


def _links(page):
    out = []
    seen = set()
    for path, ident in _DETAIL_RE.findall(page):
        if path in seen:
            continue
        seen.add(path)
        slug = path[1:].rsplit("/", 1)[0]
        out.append((slug, ident, urljoin(SITE, path)))
    return out


def _find_jobposting(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type")
        if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
            return obj
        for value in obj.values():
            found = _find_jobposting(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_jobposting(value)
            if found:
                return found
    return None


def _jobpostings(page):
    out = []
    for raw in _LDJSON_RE.findall(page):
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        job = _find_jobposting(data)
        if job:
            out.append(job)
    return out


def _org_name(job):
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return _text(org.get("name"))
    return _text(org)


def _match_link(job, links, used):
    title_slug = _slugify(job.get("title") or "")
    if not title_slug:
        return None
    cands = [
        row
        for row in links
        if row[2] not in used
        and (row[0] == title_slug or row[0].startswith(title_slug + "-"))
    ]
    if len(cands) > 1:
        org = _org_name(job) or ""
        tokens = [t for t in _slugify(org).split("-") if len(t) > 2]
        narrowed = [row for row in cands if any(t in row[0] for t in tokens)]
        if narrowed:
            cands = narrowed
    if len(cands) == 1:
        return cands[0]
    return None


def _location(job):
    parts = []
    jl = job.get("jobLocation")
    for place in jl if isinstance(jl, list) else [jl]:
        if not isinstance(place, dict):
            continue
        addr = place.get("address")
        if isinstance(addr, dict):
            bits = [
                addr.get("streetAddress"),
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            bits = list(
                dict.fromkeys(str(b).strip() for b in bits if b and str(b).strip())
            )
            if bits:
                parts.append(", ".join(bits))
        elif isinstance(addr, str) and addr.strip():
            parts.append(addr.strip())
    alr = job.get("applicantLocationRequirements")
    for req in alr if isinstance(alr, list) else [alr]:
        if isinstance(req, dict) and req.get("name"):
            parts.append(str(req["name"]).strip())
    if job.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    seen = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return "; ".join(seen) or None


def _num(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _salary(job):
    base = job.get("baseSalary")
    if not isinstance(base, dict):
        return {}
    value = base.get("value")
    if not isinstance(value, dict):
        return {}
    currency = base.get("currency")
    unit = value.get("unitText")
    min_value = value.get("minValue")
    max_value = value.get("maxValue")
    single = value.get("value")
    amount = None
    if (
        isinstance(min_value, (int, float))
        and isinstance(max_value, (int, float))
        and min_value != max_value
    ):
        amount = f"{_num(min_value)}-{_num(max_value)}"
    elif isinstance(min_value, (int, float)):
        amount = _num(min_value)
    elif isinstance(max_value, (int, float)):
        amount = _num(max_value)
    elif isinstance(single, (int, float)):
        amount = _num(single)
    out = {}
    pieces = [str(p) for p in (currency, amount, unit) if p]
    if pieces:
        out["salary"] = " ".join(pieces)
    if isinstance(min_value, (int, float)):
        out["salary_min"] = float(min_value)
    if isinstance(max_value, (int, float)):
        out["salary_max"] = float(max_value)
    if currency:
        out["salary_currency"] = str(currency).strip() or None
    if unit:
        out["salary_period"] = str(unit).strip() or None
    return out


def _clean(data):
    return {k: v for k, v in data.items() if v is not None and v != ""}


def crawl(ctx):
    seen = set()
    for listing in _LISTINGS:
        try:
            page = ctx.fetch(listing)
        except Exception:
            continue
        links = _links(page)
        for job in _jobpostings(page):
            match = _match_link(job, links, seen)
            if not match:
                continue
            _slug, ident, url = match
            title = _text(job.get("title"))
            company = _org_name(job)
            location = _location(job)
            description = _text(job.get("description"))
            if not (title and company and location and description):
                continue
            seen.add(url)
            employment = job.get("employmentType")
            if isinstance(employment, list):
                employment = ", ".join(str(x) for x in employment if x)
            out = {
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": "web3career",
                "external_id": ident,
                "employment_type": _text(employment),
                "workplace_type": _text(job.get("jobLocationType")),
                "posted_at": _iso(job.get("datePosted")),
                "expires_at": _iso(job.get("validThrough")),
            }
            out.update(_salary(job))
            yield _clean(out)
