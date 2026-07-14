
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/workmotion"
EXTRA_HOSTS = []


def _decode(response):
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="replace")
    return response


def _plain_text(*sections):
    text = "\n".join(section for section in sections if section)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|"
        r"/ul|/ol|hr)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _location_name(location):
    if not isinstance(location, dict):
        return None
    values = []
    for key in ("city", "region", "country"):
        value = location.get(key)
        if value and value not in values:
            values.append(value)
    return ", ".join(values) if values else None


def crawl(ctx):
    board = json.loads(_decode(ctx.fetch(SITE)))
    details = {}

    for listing in board.get("jobs", []):
        shortcode = listing.get("shortcode")
        if not shortcode:
            continue

        if shortcode not in details:
            detail_url = (
                "https://apply.workable.com/api/v1/accounts/workmotion/jobs/"
                + shortcode
            )
            details[shortcode] = json.loads(_decode(ctx.fetch(detail_url)))
        detail = details[shortcode]

        if detail.get("state") != "published":
            continue

        description = _plain_text(
            detail.get("description"),
            detail.get("requirements"),
            detail.get("benefits"),
        )
        if not description:
            continue

        listed_locations = []
        for item in listing.get("locations", []):
            name = _location_name(item)
            if name:
                listed_locations.append(name)

        location = _location_name({
            "city": listing.get("city"),
            "region": listing.get("state"),
            "country": listing.get("country"),
        })
        if not location and listed_locations:
            location = listed_locations[0]
        if not location:
            location = _location_name(detail.get("location"))
        if not location:
            continue

        job_data = {
            "url": listing.get("url") or listing.get("shortlink"),
            "title": listing.get("title") or detail.get("title"),
            "company": board.get("name") or "WorkMotion",
            "location": location,
            "description": description,
            "source": "Workable",
            "external_id": shortcode,
            "apply_url": listing.get("application_url"),
            "posted_at": listing.get("published_on"),
        }

        if listed_locations:
            job_data["locations"] = listed_locations

        department = listing.get("department")
        if department:
            job_data["department"] = department

        employment_type = listing.get("employment_type")
        if employment_type:
            job_data["employment_type"] = employment_type

        workplace = detail.get("workplace")
        if workplace:
            job_data["workplace_type"] = workplace
        elif listing.get("telecommuting") is True:
            job_data["workplace_type"] = "remote"

        yield {
            key: value
            for key, value in job_data.items()
            if value is not None and value != ""
        }
