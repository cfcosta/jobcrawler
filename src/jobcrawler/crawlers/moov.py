
import html
import json
import re
from urllib.parse import quote

SITE = "https://ats.rippling.com/moov/jobs"
EXTRA_HOSTS = []

_API = "https://ats.rippling.com/api/v2/board/moov/jobs"
_COMPANY = "Moov"


def _text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:p|div|section|article|ul|ol|h[1-6]|tr|table|"
        r"blockquote)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _first(record, names):
    if not isinstance(record, dict):
        return None
    for name in names:
        value = record.get(name)
        if value is not None and value != "" and value != [] and value != {}:
            return value
    return None


def _unwrap(payload):
    if not isinstance(payload, dict):
        return {}
    for key in ("job", "item", "data", "jobPost", "jobPosting"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return payload


def _description(record):
    value = _first(record, (
        "description", "jobDescription", "descriptionHtml", "descriptionHTML",
        "descriptionText", "descriptionPlain", "jobDescriptionHtml",
        "jobDescriptionHTML", "jobDescriptionText", "content",
    ))
    return _text(value)


def _find_job_record(value, job_id):
    best = None
    best_score = -1
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, dict):
            current_id = _first(current, ("id", "jobId", "jobPostId", "uuid"))
            desc = _description(current)
            score = 0
            if current_id is not None and str(current_id) == str(job_id):
                score += 10
            if desc:
                score += 5
            if _first(current, ("title", "jobTitle", "name", "positionTitle")):
                score += 2
            if score > best_score and desc:
                best = current
                best_score = score
            stack.extend(current.values())
    return best or {}


def _detail(ctx, item, job_id):
    if _description(item):
        return item

    detail_url = _API + "/" + quote(str(job_id), safe="")
    try:
        payload = json.loads(ctx.fetch(detail_url))
        detail = _unwrap(payload)
        if _description(detail):
            merged = dict(item)
            merged.update(detail)
            return merged
    except Exception:
        pass

    page_url = SITE + "/" + quote(str(job_id), safe="")
    try:
        page = ctx.fetch(page_url)
        match = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            page,
            re.S,
        )
        if match:
            detail = _find_job_record(json.loads(html.unescape(match.group(1))), job_id)
            if detail:
                merged = dict(item)
                merged.update(detail)
                return merged
    except Exception:
        pass
    return item


def _location_text(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        direct = _first(value, (
            "displayName", "name", "label", "fullName", "formattedAddress",
            "location",
        ))
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts = []
        for key in ("city", "state", "stateCode", "province", "country",
                    "countryCode"):
            part = value.get(key)
            if isinstance(part, str) and part.strip() and part.strip() not in parts:
                parts.append(part.strip())
        return ", ".join(parts) or None
    return None


def _locations(record):
    raw = _first(record, (
        "locations", "workLocations", "allLocations", "secondaryLocations",
    ))
    result = []
    if isinstance(raw, list):
        for entry in raw:
            text = _location_text(entry)
            if text and text not in result:
                result.append(text)
    elif raw is not None:
        text = _location_text(raw)
        if text:
            result.append(text)
    primary_raw = _first(record, (
        "location", "primaryLocation", "workLocation", "locationName",
    ))
    primary = _location_text(primary_raw)
    if primary and primary not in result:
        result.insert(0, primary)
    return primary or (result[0] if result else None), result


def _named(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _location_text(value)
    return None


def _salary_fields(record, fields):
    salary = _first(record, ("salary", "salaryText", "compensationText"))
    if isinstance(salary, str) and salary.strip():
        fields["salary"] = salary.strip()

    pay = _first(record, ("salaryRange", "payRange", "compensation"))
    if not isinstance(pay, dict):
        pay = record
    minimum = _first(pay, ("min", "minimum", "minValue", "salaryMin"))
    maximum = _first(pay, ("max", "maximum", "maxValue", "salaryMax"))
    currency = _first(pay, ("currency", "currencyCode", "salaryCurrency"))
    period = _first(pay, ("period", "interval", "frequency", "salaryPeriod"))
    if isinstance(minimum, (int, float)):
        fields["salary_min"] = minimum
    if isinstance(maximum, (int, float)):
        fields["salary_max"] = maximum
    if isinstance(currency, str) and currency.strip():
        fields["salary_currency"] = currency.strip()
    if isinstance(period, str) and period.strip():
        fields["salary_period"] = period.strip()


def _job(item, ctx):
    job_id = _first(item, ("id", "jobId", "jobPostId", "uuid"))
    if job_id is None:
        return None
    record = _detail(ctx, item, job_id)
    title = _first(record, ("title", "jobTitle", "name", "positionTitle"))
    description = _description(record)
    location, locations = _locations(record)
    if not isinstance(title, str) or not title.strip() or not description or not location:
        return None

    job_url = SITE + "/" + quote(str(job_id), safe="")
    fields = {
        "url": job_url,
        "title": title.strip(),
        "company": _COMPANY,
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": str(job_id),
    }
    if len(locations) > 1:
        fields["locations"] = locations

    apply_url = _first(record, ("applyUrl", "applicationUrl", "applyURL"))
    if not isinstance(apply_url, str) or not apply_url.strip():
        apply_url = job_url + "/apply"
    fields["apply_url"] = apply_url

    optional = (
        ("department", ("department", "departmentName", "team", "teamName")),
        ("employment_type", ("employmentType", "jobType", "commitment")),
        ("workplace_type", ("workplaceType", "workLocationType", "remoteType")),
        ("posted_at", ("postedAt", "publishedAt", "datePosted", "createdAt")),
        ("expires_at", ("expiresAt", "expirationDate", "validThrough")),
    )
    for output_name, aliases in optional:
        value = _first(record, aliases)
        if output_name == "department":
            value = _named(value)
        if isinstance(value, str) and value.strip():
            fields[output_name] = value.strip()

    _salary_fields(record, fields)
    return Job(**fields)


def crawl(ctx):
    page = 0
    page_size = 1000
    while True:
        url = _API + "?page=" + str(page) + "&pageSize=" + str(page_size)
        payload = json.loads(ctx.fetch(url))
        items = payload.get("items")
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                job = _job(item, ctx)
                if job is not None:
                    yield job

        total_pages = payload.get("totalPages")
        if isinstance(total_pages, int):
            if page + 1 >= total_pages:
                return
        elif len(items) < page_size:
            return
        if not items:
            return
        page += 1
