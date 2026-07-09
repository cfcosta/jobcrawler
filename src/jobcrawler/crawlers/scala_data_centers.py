import json
import re
import html
from urllib.parse import urljoin

SITE = "https://scaladatacenters.gupy.io/"
EXTRA_HOSTS = []

COMPANY = "Scala Data Centers"


def _next_data(text):
    m = re.search(
        r'<script\s+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S | re.I
    )
    if not m:
        return {}
    raw = html.unescape(m.group(1))
    return json.loads(raw)


def _strip_html(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _location_from_board(job):
    workplace = job.get("workplace") or {}
    address = workplace.get("address") or {}
    parts = []
    for key in ("city", "stateShortName", "state", "country"):
        val = address.get(key)
        if val and val not in parts:
            parts.append(val)
    return ", ".join(parts) if parts else None


def _location_from_detail(job):
    parts = []
    for val in (
        job.get("addressCity"),
        job.get("addressStateShortName") or job.get("addressState"),
        job.get("addressCountry"),
    ):
        if val and val not in parts:
            parts.append(val)
    return ", ".join(parts) if parts else None


def _pick(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _set(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    root_html = ctx.fetch(SITE)
    data = _next_data(root_html)
    page_props = (data.get("props") or {}).get("pageProps") or {}
    jobs = page_props.get("jobs") or []

    for board_job in jobs:
        if board_job.get("type") and board_job.get("type") != "vacancy_type_effective":
            continue

        job_id = board_job.get("id")
        if not job_id:
            continue

        url = urljoin(SITE, "jobs/%s?jobBoardSource=gupy_public_page" % job_id)
        detail_html = ctx.fetch(urljoin(SITE, "jobs/%s" % job_id))
        detail_data = _next_data(detail_html)
        detail_props = (detail_data.get("props") or {}).get("pageProps") or {}
        detail_job = detail_props.get("job") or {}

        if detail_job.get("status") and detail_job.get("status") != "published":
            continue

        sections = [
            detail_job.get("description"),
            detail_job.get("responsibilities"),
            detail_job.get("prerequisites"),
            detail_job.get("relevantExperiences"),
        ]
        description = "\n\n".join(
            part for part in (_strip_html(s) for s in sections) if part
        )
        if not description:
            continue

        title = _pick(board_job.get("title"), detail_job.get("name"))
        location = _pick(
            _location_from_board(board_job), _location_from_detail(detail_job)
        )
        if not title or not location:
            continue

        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }
        _set(
            item,
            "department",
            _pick(board_job.get("department"), detail_job.get("department")),
        )
        _set(
            item,
            "workplace_type",
            _pick(
                (board_job.get("workplace") or {}).get("workplaceType"),
                detail_job.get("workplaceType"),
            ),
        )
        _set(item, "employment_type", detail_job.get("jobType"))
        _set(item, "posted_at", detail_job.get("publishedAt"))
        _set(
            item,
            "expires_at",
            detail_job.get("expiresAt") or detail_job.get("registerEndDate"),
        )
        _set(item, "external_id", str(job_id))
        _set(item, "apply_url", url)
        yield item
