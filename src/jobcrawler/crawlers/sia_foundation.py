import html
import json
import re
from datetime import datetime, timezone


SITE = "https://api.lever.co/v0/postings/the-sia-foundation?mode=json"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _full_description(posting):
    sections = []

    intro = _plain_text(posting.get("description"))
    if intro:
        sections.append(intro)

    for section in posting.get("lists") or []:
        heading = _plain_text(section.get("text"))
        content = _plain_text(section.get("content"))
        if heading and content:
            sections.append(heading + "\n" + content)
        elif heading or content:
            sections.append(heading or content)

    additional = _plain_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections).strip()


def _iso_epoch_ms(value):
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(
            float(value) / 1000, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    if not isinstance(postings, list):
        raise ValueError("Lever postings API did not return a list")

    for posting in postings:
        categories = posting.get("categories") or {}
        kwargs = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "The Sia Foundation",
            "location": categories.get("location"),
            "description": _full_description(posting),
            "source": "Lever",
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [
                value for value in all_locations
                if isinstance(value, str) and value.strip()
            ]
            if locations:
                kwargs["locations"] = locations

        optional = {
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _iso_epoch_ms(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        kwargs.update({
            key: value for key, value in optional.items()
            if value is not None and value != ""
        })

        salary_range = posting.get("salaryRange")
        if isinstance(salary_range, dict):
            salary_fields = {
                "salary_min": salary_range.get("min"),
                "salary_max": salary_range.get("max"),
                "salary_currency": salary_range.get("currency"),
                "salary_period": salary_range.get("interval"),
            }
            kwargs.update({
                key: value for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        yield Job(**kwargs)
