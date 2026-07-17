import html
import json
import re

SITE = "https://storyteller.applytojob.com/"
EXTRA_HOSTS = []

_SOURCE = "storyteller"
_COMPANY = "Storyteller"

# JazzHR lists every posting as an /apply/<id>/<slug> link but carries the body
# only on the per-posting page (JobPosting JSON-LD), so each needs one detail GET.
# applytojob.com throttles those detail pages to ~1 per 2s by *stalling* the
# response (a time-based rate limit, not a 429 — no header or fresh-connection
# trick avoids it), so a crawl can only hydrate ~4 details inside the 10s
# validation wall. Hydrate the freshest few links and stop. The board is
# registered enumerates_all:false — a windowed slice, rechecked per-row by the
# §4.9 sweep, never diff-closed — so the throttled tail is never mistaken for
# closed postings.
_DETAIL_CAP = 3

_APPLY_RE = re.compile(
    r"https?://storyteller\.applytojob\.com/apply/([A-Za-z0-9]+)/([A-Za-z0-9-]+)"
)
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>|</div\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()


def _job_posting(page):
    for block in _LDJSON_RE.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _country(node):
    """Country name from an applicantLocationRequirements node (dict or list)."""
    if isinstance(node, list):
        node = node[0] if node else None
    if isinstance(node, dict):
        return node.get("name")
    return None


def _location(posting):
    address = (posting.get("jobLocation") or {}).get("address") or {}
    parts = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    physical = ", ".join(p for p in parts if p)
    if physical:
        return physical, None
    # Remote posting: distinguish by the country it is open to.
    if posting.get("jobLocationType") == "TELECOMMUTE":
        country = _country(posting.get("applicantLocationRequirements"))
        return (f"Remote ({country})" if country else "Remote"), "Remote"
    return None, None


def crawl(ctx):
    index = ctx.fetch(SITE)
    seen = set()
    ordered = []
    for job_id, slug in _APPLY_RE.findall(index):
        if job_id not in seen:
            seen.add(job_id)
            ordered.append((job_id, slug))

    yielded = 0
    for job_id, slug in ordered:
        if yielded >= _DETAIL_CAP:
            break
        url = f"https://storyteller.applytojob.com/apply/{job_id}/{slug}"
        page = ctx.fetch(url)
        posting = _job_posting(page)
        if not posting:
            continue

        title = posting.get("title")
        description = _text(posting.get("description"))
        location, workplace_type = _location(posting)
        if not title or not description or not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": _COMPANY,
            "location": location,
            "description": description,
            "source": _SOURCE,
            "apply_url": url,
            "external_id": job_id,
        }
        if workplace_type:
            job["workplace_type"] = workplace_type
        posted_at = posting.get("datePosted")
        if posted_at:
            job["posted_at"] = posted_at
        employment_type = posting.get("employmentType")
        if employment_type:
            job["employment_type"] = employment_type

        yielded += 1
        yield job
