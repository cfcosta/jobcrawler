import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev"
EXTRA_HOSTS = []

_BLOCK_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|"
    r"tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&(#(?:x[0-9a-f]+|\d+)|[a-z]+);", re.IGNORECASE)
_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "hellip": "...",
    "lt": "<",
    "nbsp": " ",
    "quot": '"',
    "rsquo": "'",
    "lsquo": "'",
    "rdquo": '"',
    "ldquo": '"',
    "ndash": "-",
    "mdash": "-",
}


def _decode_entity(match):
    value = match.group(1)
    if value.startswith(("#x", "#X")):
        try:
            return chr(int(value[2:], 16))
        except (ValueError, OverflowError):
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except (ValueError, OverflowError):
            return match.group(0)
    return _ENTITIES.get(value.lower(), match.group(0))


def _html_to_text(value):
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = _ENTITY_RE.sub(_decode_entity, value)
    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _first_text(value):
    if isinstance(value, list):
        value = next((item for item in value if item), None)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    seen = set()

    for item in payload.get("jobs") or []:
        url = _first_text(item.get("url"))
        title = _first_text(item.get("jobTitle") or item.get("title"))
        company = _first_text(item.get("companyName") or item.get("company"))
        location = _first_text(item.get("jobGeo") or item.get("location"))
        body = item.get("jobDescription") or item.get("description")
        description = _html_to_text(body) if isinstance(body, str) else None
        external_id = _first_text(item.get("id") or item.get("jobSlug"))

        dedupe_key = external_id or url
        if (
            not url
            or not title
            or not company
            or not location
            or not description
            or len(description) < 200
            or dedupe_key in seen
        ):
            continue
        seen.add(dedupe_key)

        kwargs = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": external_id,
            "posted_at": _first_text(item.get("pubDate") or item.get("postedAt")),
            "employment_type": _first_text(
                item.get("jobType") or item.get("employmentType")
            ),
            "department": _first_text(
                item.get("jobIndustry") or item.get("department")
            ),
        }

        salary_min = item.get("salaryMin")
        salary_max = item.get("salaryMax")
        salary_currency = _first_text(item.get("salaryCurrency"))
        salary_period = _first_text(item.get("salaryPeriod"))
        if salary_min is not None:
            kwargs["salary_min"] = salary_min
        if salary_max is not None:
            kwargs["salary_max"] = salary_max
        if salary_currency:
            kwargs["salary_currency"] = salary_currency
        if salary_period:
            kwargs["salary_period"] = salary_period

        yield Job(**{key: value for key, value in kwargs.items() if value is not None})
