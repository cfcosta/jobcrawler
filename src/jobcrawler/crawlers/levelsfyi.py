import html
import json
import re

SITE = "https://www.levels.fyi/jobs"
EXTRA_HOSTS = []

MAX_JOBS = 6

# The Next.js origin fingerprints non-browser clients (empty 200), and the CDN
# returns a blank body for a plain gzip Accept-Encoding (no brotli variant).
# Present a browser and ask for the uncompressed representation.
_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "accept-encoding": "identity",
}

_NEXT_DATA = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return value


def _strip_html(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?is)<\s*(script|style)\b.*?</\s*\1\s*>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _page_props(page):
    match = _NEXT_DATA.search(page)
    if not match:
        return {}
    raw = match.group(1)
    try:
        data = json.loads(raw)
    except Exception:
        try:
            data = json.loads(html.unescape(raw))
        except Exception:
            return {}
    props = data.get("props") if isinstance(data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    return page_props if isinstance(page_props, dict) else {}


def _job_ids(page_props):
    jobs_data = page_props.get("initialJobsData")
    results = jobs_data.get("results") if isinstance(jobs_data, dict) else None
    ids = []
    seen = set()
    if not isinstance(results, list):
        return ids
    for group in results:
        jobs = group.get("jobs") if isinstance(group, dict) else None
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            jid = job.get("id")
            if jid is None:
                continue
            jid = str(jid)
            if jid not in seen:
                seen.add(jid)
                ids.append(jid)
    return ids


def _location(det):
    parts = []
    locations = det.get("locations")
    if isinstance(locations, list):
        for loc in locations:
            text = _clean(loc)
            if text:
                parts.append(text)
    if parts:
        return "; ".join(dict.fromkeys(parts))
    arrangement = _clean(det.get("workArrangement"))
    if arrangement:
        return arrangement.replace("_", " ").title()
    return None


def _employment_type(det):
    names = []
    types = det.get("employmentTypes")
    if isinstance(types, list):
        for entry in types:
            text = _clean(entry)
            if text:
                names.append(text)
    return ", ".join(dict.fromkeys(names)) or None


def _amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _clean_dict(data):
    return {k: v for k, v in data.items() if v is not None and v != ""}


def _fetch(ctx, url):
    # The CDN caches a blank 200 under no-store, so a shared entry poisoned by any
    # gzip client stays blank; a private query key keeps us on a healthy variant.
    sep = "&" if "?" in url else "?"
    return ctx.fetch(f"{url}{sep}ref=jobcrawler", headers=_HEADERS)


def crawl(ctx):
    listing = _fetch(ctx, SITE)
    for jid in _job_ids(_page_props(listing))[:MAX_JOBS]:
        try:
            page = _fetch(ctx, f"{SITE}?jobId={jid}")
        except Exception:
            continue
        det = _page_props(page).get("initialJobDetails")
        if not isinstance(det, dict):
            continue

        title = _clean(det.get("title"))
        company_info = det.get("companyInfo")
        company = (
            _clean(company_info.get("name")) if isinstance(company_info, dict) else None
        )
        location = _location(det)
        description = _strip_html(det.get("description"))
        if not (title and company and location and description):
            continue

        external_id = _clean(det.get("id")) or jid
        salary_min = _amount(det.get("minBaseSalary"))
        salary_max = _amount(det.get("maxBaseSalary"))
        currency = _clean(det.get("baseSalaryCurrency"))

        out = {
            "url": f"{SITE}?jobId={external_id}",
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": "levelsfyi",
            "external_id": str(external_id),
            "workplace_type": _clean(det.get("workArrangement")),
            "employment_type": _employment_type(det),
            "posted_at": _clean(det.get("postingDate")),
            "expires_at": _clean(det.get("expiryDate")),
            "apply_url": _clean(det.get("applicationUrl")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": currency if (salary_min or salary_max) else None,
        }
        yield _clean_dict(out)
