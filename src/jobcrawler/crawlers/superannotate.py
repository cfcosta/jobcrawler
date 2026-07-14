import html
import json
import re
from datetime import datetime, timezone

SITE = 'https://api.lever.co/v0/postings/superannotate?mode=json'
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    value = re.sub(r"(?is)<\s*li[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?is)<\s*/?\s*(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|main|nav|ol|p|pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(
        value / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    response = ctx.fetch(SITE)
    records = json.loads(response)

    for record in records:
        categories = record.get("categories") or {}
        sections = []

        intro = _plain(record.get("description"))
        if intro:
            sections.append(intro)

        for section in record.get("lists") or []:
            heading = _plain(section.get("text"))
            content = _plain(section.get("content"))
            if heading and content:
                sections.append(heading + "\n" + content)
            elif content:
                sections.append(content)
            elif heading:
                sections.append(heading)

        additional = _plain(record.get("additional"))
        if additional:
            sections.append(additional)

        job = {
            "url": record.get("hostedUrl"),
            "title": record.get("text"),
            "company": "SuperAnnotate",
            "location": categories.get("location"),
            "description": "\n\n".join(sections).strip(),
            "source": SITE,
        }

        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [value for value in all_locations if value]
            if locations:
                job["locations"] = locations

        optional = {
            "external_id": record.get("id"),
            "apply_url": record.get("applyUrl"),
            "workplace_type": record.get("workplaceType"),
            "employment_type": categories.get("commitment"),
            "department": categories.get("department") or categories.get("team"),
            "posted_at": _posted_at(record.get("createdAt")),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                job[key] = value

        salary_range = record.get("salaryRange")
        if isinstance(salary_range, dict):
            salary_fields = {
                "salary_min": salary_range.get("min"),
                "salary_max": salary_range.get("max"),
                "salary_currency": salary_range.get("currency"),
                "salary_period": salary_range.get("interval"),
            }
            for key, value in salary_fields.items():
                if value is not None and value != "":
                    job[key] = value

        salary = _plain(record.get("salaryDescription"))
        if salary:
            job["salary"] = salary

        yield job
