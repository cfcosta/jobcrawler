
import json
import re
from html import unescape

SITE = 'https://apply.workable.com/api/v1/widget/accounts/floatjobs'
EXTRA_HOSTS = []

_ACCOUNT = "floatjobs"
_DETAIL_ROOT = "https://apply.workable.com/api/v1/accounts/floatjobs/jobs/"
_POSTING_ROOT = "https://apply.workable.com/floatjobs/j/"


def _plain_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if not value.strip():
        return None

    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\r", "")

    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip() or None


def _first(mapping, names):
    for name in names:
        value = mapping.get(name)
        if value is not None and value != "":
            return value
    return None


def _location_text(value):
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    display = _first(value, ("location", "name", "display_name", "full_location"))
    if isinstance(display, str) and display.strip():
        return display.strip()

    parts = []
    for key in ("city", "state", "region", "country"):
        part = value.get(key)
        if isinstance(part, str) and part.strip() and part.strip() not in parts:
            parts.append(part.strip())
    return ", ".join(parts) or None


def _locations(*records):
    result = []
    for record in records:
        values = record.get("locations")
        if not isinstance(values, list):
            continue
        for value in values:
            text = _location_text(value)
            if text and text not in result:
                result.append(text)
    return result


def _put(job, key, value):
    if value is not None and value != "":
        job[key] = value


def _salary_fields(job, detail, listing):
    salary = detail.get("salary")
    if salary is None:
        salary = listing.get("salary")

    if isinstance(salary, str):
        _put(job, "salary", salary.strip())
        return

    if not isinstance(salary, dict):
        salary = {}

    printed = _first(salary, ("display", "text", "formatted", "label"))
    if isinstance(printed, str):
        _put(job, "salary", printed.strip())

    minimum = _first(
        salary,
        ("salary_min", "min", "minimum", "from", "salary_from", "min_value"),
    )
    maximum = _first(
        salary,
        ("salary_max", "max", "maximum", "to", "salary_to", "max_value"),
    )
    currency = _first(
        salary,
        ("salary_currency", "currency", "currency_code", "currencyCode"),
    )
    period = _first(
        salary,
        ("salary_period", "period", "interval", "frequency", "unit"),
    )

    if minimum is None:
        minimum = _first(detail, ("salary_min", "salary_from"))
    if maximum is None:
        maximum = _first(detail, ("salary_max", "salary_to"))
    if currency is None:
        currency = _first(detail, ("salary_currency", "currency"))
    if period is None:
        period = _first(detail, ("salary_period",))

    _put(job, "salary_min", minimum)
    _put(job, "salary_max", maximum)
    _put(job, "salary_currency", currency)
    _put(job, "salary_period", period)


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE + "?details=true"))
    seen = set()

    for listing in board.get("jobs", []):
        if not isinstance(listing, dict):
            continue

        shortcode = listing.get("shortcode")
        if not isinstance(shortcode, str) or not shortcode.strip():
            continue
        shortcode = shortcode.strip()
        if shortcode in seen:
            continue
        seen.add(shortcode)

        detail = json.loads(ctx.fetch(_DETAIL_ROOT + shortcode))
        if not isinstance(detail, dict):
            continue

        title = _first(detail, ("title", "full_title"))
        if title is None:
            title = listing.get("title")
        if not isinstance(title, str) or not title.strip():
            continue

        sections = []
        for key in ("description", "requirements", "benefits"):
            text = _plain_text(detail.get(key))
            if text:
                sections.append(text)
        description = "\n\n".join(sections).strip()
        if not description:
            continue

        canonical_url = _first(
            detail,
            ("url", "shortlink", "job_url", "canonical_url"),
        )
        if not isinstance(canonical_url, str) or not canonical_url.strip():
            canonical_url = _first(
                listing,
                ("url", "shortlink", "job_url", "canonical_url"),
            )
        if not isinstance(canonical_url, str) or not canonical_url.strip():
            canonical_url = _POSTING_ROOT + shortcode

        all_locations = _locations(detail, listing)
        primary_location = _location_text(detail.get("location"))
        if not primary_location:
            primary_location = _location_text(listing.get("location"))
        if not primary_location:
            primary_location = _location_text(detail)
        if not primary_location:
            primary_location = _location_text(listing)
        if not primary_location and all_locations:
            primary_location = all_locations[0]

        telecommuting = detail.get("telecommuting")
        if telecommuting is None:
            telecommuting = listing.get("telecommuting")
        if not primary_location and telecommuting is True:
            primary_location = "Remote"
        if not primary_location:
            continue

        job = {
            "url": canonical_url.strip(),
            "title": title.strip(),
            "company": "Float.com",
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": shortcode,
        }

        if all_locations:
            job["locations"] = all_locations

        department = _first(detail, ("department", "function"))
        if department is None:
            department = _first(listing, ("department", "function"))
        _put(job, "department", department)

        employment_type = _first(
            detail, ("employment_type", "employmentType", "contract_type")
        )
        if employment_type is None:
            employment_type = _first(
                listing, ("employment_type", "employmentType", "contract_type")
            )
        _put(job, "employment_type", employment_type)

        workplace_type = _first(
            detail, ("workplace_type", "workplaceType")
        )
        if workplace_type is None:
            workplace_type = _first(
                listing, ("workplace_type", "workplaceType")
            )
        if workplace_type is None and telecommuting is True:
            workplace_type = "remote"
        _put(job, "workplace_type", workplace_type)

        apply_url = _first(
            detail, ("application_url", "apply_url", "applicationUrl", "url")
        )
        if apply_url is None:
            apply_url = _first(
                listing, ("application_url", "apply_url", "applicationUrl", "url")
            )
        _put(job, "apply_url", apply_url)

        posted_at = _first(
            detail,
            ("published_at", "published_on", "posted_at", "created_at", "publishedAt", "createdAt"),
        )
        if posted_at is None:
            posted_at = _first(
                listing,
                ("published_at", "published_on", "posted_at", "created_at", "publishedAt", "createdAt"),
            )
        _put(job, "posted_at", posted_at)

        expires_at = _first(
            detail,
            ("expires_at", "expiration_date", "expiresAt", "expirationDate"),
        )
        if expires_at is None:
            expires_at = _first(
                listing,
                ("expires_at", "expiration_date", "expiresAt", "expirationDate"),
            )
        _put(job, "expires_at", expires_at)

        _salary_fields(job, detail, listing)
        yield job
