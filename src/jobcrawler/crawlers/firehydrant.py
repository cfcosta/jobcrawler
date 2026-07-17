
import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from jobcrawler.runtime.contract import Job


SITE = "https://ats.rippling.com/api/v2/board/firehydrant/jobs?page=0&pageSize=1000"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    text = html.unescape(str(value))
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:p|div|li|ul|ol|h[1-6]|"
        r"section|article|blockquote|pre|table|tr|td|th)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _page_url(page):
    parts = urlsplit(SITE)
    query = urlencode({"page": page, "pageSize": 1000})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _first(item, *names):
    for name in names:
        value = item.get(name)
        if value is not None and value != "":
            return value
    return None


def _location(item):
    location = item.get("location")
    if isinstance(location, str) and location.strip():
        return location.strip()

    values = []
    source = location if isinstance(location, dict) else item
    for key in ("city", "state", "country"):
        value = source.get(key)
        if value:
            value = str(value).strip()
            if value and value not in values:
                values.append(value)

    if values:
        return ", ".join(values)

    workplace = item.get("workplaceType")
    if workplace and str(workplace).upper() == "REMOTE":
        return "Remote"
    return None


def crawl(ctx):
    page = 0

    while True:
        response = ctx.fetch(_page_url(page))
        data = json.loads(response)
        items = data.get("items") or []

        for item in items:
            job_id = _first(item, "id", "jobId")
            title = _first(item, "name", "title")
            description = _plain_text(
                _first(item, "description", "descriptionHtml", "jobDescription")
            )
            location = _location(item)

            if not job_id or not title or not description or not location:
                continue

            posting_url = (
                "https://ats.rippling.com/firehydrant/jobs/"
                + str(job_id)
                + "/apply"
            )
            fields = {
                "url": posting_url,
                "title": str(title),
                "company": "FireHydrant",
                "location": location,
                "description": description,
                "source": "ats.rippling.com",
                "external_id": str(job_id),
                "apply_url": posting_url,
                "department": _first(item, "department", "departmentName"),
                "employment_type": _first(
                    item, "employmentType", "employment_type"
                ),
                "workplace_type": _first(
                    item, "workplaceType", "workplace_type"
                ),
                "posted_at": _first(
                    item, "publishedAt", "postedAt", "createdAt"
                ),
                "expires_at": _first(item, "expiresAt", "expirationDate"),
            }
            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None and value != ""
            })

        total_pages = data.get("totalPages")
        if isinstance(total_pages, int):
            if page + 1 >= total_pages:
                break
        elif len(items) < 1000:
            break

        page += 1
