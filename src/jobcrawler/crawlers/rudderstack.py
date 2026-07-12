import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit


SITE = "https://ats.rippling.com/api/v2/board/rudderstack-careers/jobs?page=0&pageSize=1000"
EXTRA_HOSTS = []

_BOARD_SLUG = "rudderstack-careers"
_COMPANY = "RudderStack"


def _plain_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _value(item, *names):
    for name in names:
        value = item.get(name)
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return None


def _label(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _label(_value(value, "name", "label", "value", "title"))
    return None


def _location(item):
    supplied = _value(item, "location", "locationName")
    label = _label(supplied)
    if label:
        return label

    parts = []
    for key in ("city", "state", "country"):
        part = _label(item.get(key))
        if part and part not in parts:
            parts.append(part)
    return ", ".join(parts) or None


def _page_url(page):
    parsed = urlsplit(SITE)
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode({"page": page, "pageSize": 1000}),
        "",
    ))


def crawl(ctx):
    page = 0
    while True:
        payload = json.loads(ctx.fetch(_page_url(page)))
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("Rippling response is missing its items list")

        for item in items:
            if not isinstance(item, dict):
                continue

            job_id = _value(item, "id", "jobId")
            title = _label(_value(item, "name", "title"))
            description = _plain_text(
                _value(item, "description", "descriptionHtml", "jobDescription")
            )
            location = _location(item)
            if not job_id or not title or not description or not location:
                continue

            url = (
                "https://ats.rippling.com/"
                + _BOARD_SLUG
                + "/jobs/"
                + str(job_id)
                + "/apply"
            )
            fields = {
                "url": url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": url,
            }
            optional = {
                "department": _label(
                    _value(item, "department", "departmentName")
                ),
                "workplace_type": _label(item.get("workplaceType")),
                "employment_type": _label(
                    _value(item, "employmentType", "jobType")
                ),
                "posted_at": _value(
                    item, "postedAt", "publishedAt", "datePosted"
                ),
                "expires_at": _value(
                    item, "expiresAt", "expirationDate"
                ),
            }
            fields.update({
                key: value
                for key, value in optional.items()
                if value is not None
            })
            yield Job(**fields)

        total_pages = payload.get("totalPages")
        if not isinstance(total_pages, int) or total_pages <= page + 1:
            break
        page += 1
