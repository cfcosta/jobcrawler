
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://api.gem.com/job_board/v0/dydx/job_posts/"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _description(post):
    plain = post.get("content_plain")
    if isinstance(plain, str) and plain.strip():
        return html.unescape(plain).strip()

    content = post.get("content")
    if isinstance(content, str) and content.strip():
        return _html_to_text(content)
    return None


def _names(values):
    result = []
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name")
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def crawl(ctx):
    try:
        body = ctx.fetch(SITE)
    except Exception as error:
        if "404 Not Found" in str(error):
            return
        raise
    posts = json.loads(body)
    if not isinstance(posts, list):
        raise ValueError("Gem job board response is not a list")

    for post in posts:
        if not isinstance(post, dict):
            continue

        title = post.get("title")
        url = post.get("absolute_url")
        description = _description(post)
        location_data = post.get("location")
        location = (
            location_data.get("name")
            if isinstance(location_data, dict)
            else location_data
        )

        required = (title, url, description, location)
        if not all(isinstance(value, str) and value.strip() for value in required):
            continue

        data = {
            "title": title.strip(),
            "company": "dYdX",
            "location": location.strip(),
            "description": description,
        }

        offices = _names(post.get("offices"))
        if offices:
            data["locations"] = offices

        departments = _names(post.get("departments"))
        if departments:
            data["department"] = ", ".join(departments)

        optional = {
            "employment_type": post.get("employment_type"),
            "workplace_type": post.get("location_type"),
            "posted_at": post.get("first_published_at"),
            "external_id": post.get("id"),
            "apply_url": post.get("absolute_url"),
        }
        for key, value in optional.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            data[key] = str(value) if key == "external_id" else value

        yield Job(url=url.strip(), **data)
