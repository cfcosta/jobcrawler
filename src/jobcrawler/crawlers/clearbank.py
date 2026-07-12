import html
import json
import re


SITE = "https://api.ashbyhq.com/posting-api/job-board/clearbank?includeCompensation=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        description = (posting.get("descriptionPlain") or "").strip()
        if not description:
            description = _html_to_text(posting.get("descriptionHtml") or "")
        if not description:
            continue

        primary_location = posting.get("location")
        locations = []
        if primary_location:
            locations.append(primary_location)

        for secondary in posting.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else secondary
            if value and value not in locations:
                locations.append(value)

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "ClearBank",
            "location": primary_location,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "employment_type": posting.get("employmentType"),
            "workplace_type": workplace_type,
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
