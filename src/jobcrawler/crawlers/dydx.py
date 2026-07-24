
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://api.gem.com/job_board/v0/dydx/job_posts/"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|tr)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _names(items):
    result = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else item
        if name and name not in result:
            result.append(name)
    return result


def crawl(ctx):
    try:
        response = ctx.fetch(SITE)
    except Exception as exc:
        if "404" in str(exc) or "not found" in str(exc).lower():
            return
        raise

    if hasattr(response, "json"):
        data = response.json()
    else:
        raw = response.text if hasattr(response, "text") else response
        data = json.loads(raw)

    if isinstance(data, dict):
        if data.get("code") == 404 or data.get("status") == "Not Found":
            return
        postings = data.get("jobs") or data.get("job_posts") or []
    else:
        postings = data

    for posting in postings:
        title = _text(posting.get("title"))
        url = posting.get("absolute_url")
        location_obj = posting.get("location") or {}
        location = (
            location_obj.get("name")
            if isinstance(location_obj, dict)
            else location_obj
        )
        description = _text(
            posting.get("content_plain") or posting.get("content")
        )
        if not title or not url or not location or not description:
            continue

        offices = _names(posting.get("offices"))
        departments = _names(posting.get("departments"))
        kwargs = {
            "url": url,
            "title": title,
            "company": "dYdX",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
            "apply_url": url,
            "employment_type": posting.get("employment_type"),
            "workplace_type": posting.get("location_type"),
            "department": ", ".join(departments) if departments else None,
            "posted_at": posting.get("first_published_at"),
        }
        if offices:
            kwargs["locations"] = offices
        yield Job(**{key: value for key, value in kwargs.items() if value is not None})
