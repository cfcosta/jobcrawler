import html
import json
import re


SITE = "https://recruiting.paylocity.com/recruiting/v2/api/feed/jobs/982bc369-6352-4fa4-942d-7c76bfca29b4"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not isinstance(value, str) or not value.strip():
        return None

    value = re.sub(
        r"(?is)<(script|style|template)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)<\s*(br|p|div|li|h[1-6]|tr|table|ul|ol|section|article|blockquote|pre|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(p|div|li|h[1-6]|tr|table|ul|ol|section|article|blockquote|pre)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = [" ".join(line.split()) for line in value.splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    return text or None


def crawl(ctx):
    feed = json.loads(ctx.fetch(SITE))

    for item in feed.get("jobs") or []:
        location_data = item.get("jobLocation") or {}
        location = (
            location_data.get("locationDisplayName")
            or location_data.get("name")
        )

        sections = [
            text
            for text in (
                _html_to_text(item.get("description")),
                _html_to_text(item.get("requirements")),
            )
            if text
        ]
        description = "\n\n".join(sections) or None

        employment_type = next(
            (
                value.strip()
                for value in (item.get("jobTypesArray") or [])
                if isinstance(value, str) and value.strip()
            ),
            None,
        )

        fields = {
            "url": item.get("displayUrl"),
            "title": item.get("title"),
            "company": "CloudBees Inc",
            "location": location,
            "description": description,
            "source": SITE,
            "posted_at": item.get("publishedDate") or item.get("createdUtc"),
            "apply_url": item.get("applyUrl"),
            "department": item.get("hiringDepartment"),
            "employment_type": employment_type,
        }
        if item.get("jobId") is not None:
            fields["external_id"] = str(item["jobId"])

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
