
import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from jobcrawler.runtime.contract import Job



SITE = "https://join.com/api/public/companies/117532/jobs?page=1&pageSize=5"
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    raw = html.unescape(str(value))
    if "<" in raw and ">" in raw:
        raw = re.sub(
            r"(?i)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\\s*/?>",
            "\\n",
            raw,
        )
        raw = re.sub(r"(?s)<script[^>]*>.*?</script>", "", raw, flags=re.I)
        raw = re.sub(r"(?s)<style[^>]*>.*?</style>", "", raw, flags=re.I)
        raw = re.sub(r"(?s)<[^>]+>", "", raw)
        raw = html.unescape(raw)
    raw = raw.replace("\\xa0", " ")
    lines = [re.sub(r"[ \\t]+", " ", line).strip() for line in raw.splitlines()]
    cleaned = "\\n".join(line for line in lines if line)
    return cleaned or None


def _label(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "label", "title", "value"):
            result = _text(value.get(key))
            if result:
                return result
    return None


def _first(data, *keys):
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def _description(data):
    # JOIN stores the posting sections separately in some API versions.
    direct = _first(
        data, "descriptionHtml", "description", "contentHtml", "content",
        "jobDescription", "body",
    )
    parts = []
    if direct:
        value = _text(direct)
        if value:
            parts.append(value)

    section_keys = (
        ("Tasks", ("tasks", "responsibilities")),
        ("Requirements", ("requirements", "skills", "profile")),
        ("Benefits", ("benefits",)),
        ("About us", ("aboutUs", "companyDescription")),
    )
    for heading, keys in section_keys:
        value = _first(data, *keys)
        if isinstance(value, list):
            value = "\n".join(
                filter(None, (_text(item) if not isinstance(item, dict)
                              else _label(item) for item in value))
            )
        else:
            value = _text(value)
        if value and value not in parts:
            parts.append(heading + "\n" + value)

    return "\n\n".join(parts) or None


def _location(data):
    value = _first(data, "location", "workplaceLocation")
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        display = _first(
            value, "name", "formattedAddress", "displayName", "label",
            "fullAddress",
        )
        if display:
            return _text(display)
        pieces = [
            _text(value.get(key))
            for key in ("city", "state", "country")
            if value.get(key)
        ]
        if pieces:
            return ", ".join(pieces)
    return _text(_first(data, "locationName", "city"))


def _locations(data):
    raw = _first(data, "locations", "workplaceLocations")
    if not isinstance(raw, list):
        return None
    values = []
    for item in raw:
        value = _label(item) if isinstance(item, dict) else _text(item)
        if value:
            values.append(value)
    return values or None


def _canonical_url(item, detail):
    for data in (detail, item):
        value = _first(data, "url", "jobUrl", "canonicalUrl", "publicUrl")
        if isinstance(value, str) and value.startswith("https://join.com/"):
            return value

    job_id = _first(detail, "id", "jobId") or _first(item, "id", "jobId")
    slug = _first(detail, "slug") or _first(item, "slug")
    company = detail.get("company") or item.get("company") or {}
    company_slug = (
        _first(company, "slug", "domain")
        if isinstance(company, dict) else None
    ) or "cratedb"
    if job_id and slug:
        return f"https://join.com/companies/{company_slug}/{job_id}-{slug}"
    return None


def _salary_fields(data):
    salary = data.get("salary")
    if not isinstance(salary, dict):
        return {}
    result = {}
    printed = _first(salary, "formatted", "text", "displayValue", "label")
    if printed:
        result["salary"] = _text(printed)

    minimum = _first(salary, "min", "minimum", "from", "minValue")
    maximum = _first(salary, "max", "maximum", "to", "maxValue")
    currency = _first(salary, "currency", "currencyCode")
    period = _first(salary, "period", "frequency", "interval")
    if minimum is not None:
        result["salary_min"] = minimum
    if maximum is not None:
        result["salary_max"] = maximum
    if currency:
        result["salary_currency"] = _label(currency) or _text(currency)
    if period:
        result["salary_period"] = _label(period) or _text(period)
    return result


def _page_url(page):
    parts = urlsplit(SITE)
    query = urlencode({"page": page, "pageSize": 5})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def crawl(ctx):
    page = 1
    while True:
        response = ctx.fetch(_page_url(page))
        payload = json.loads(response)
        items = payload.get("items") or []
        pagination = payload.get("pagination") or {}

        for item in items:
            job_id = _first(item, "id", "jobId")
            detail = item
            if job_id is not None:
                detail_response = ctx.fetch(
                    f"https://join.com/api/public/jobs/{job_id}"
                )
                loaded = json.loads(detail_response)
                if isinstance(loaded, dict):
                    detail = loaded.get("item") or loaded.get("job") or loaded

            title = _text(_first(detail, "title", "name")) or _text(
                _first(item, "title", "name")
            )
            description = _description(detail)
            url = _canonical_url(item, detail)
            location = _location(detail) or _location(item)
            if not (url and title and description and location):
                continue

            company_data = detail.get("company") or item.get("company")
            company = _label(company_data) or "CrateDB"
            fields = {
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
            }

            optional = {
                "locations": _locations(detail) or _locations(item),
                "workplace_type": _label(_first(
                    detail, "workplaceType", "workplace"
                )),
                "employment_type": _label(_first(
                    detail, "employmentType", "contractType"
                )),
                "department": _label(_first(
                    detail, "department", "category", "team"
                )),
                "posted_at": _text(_first(
                    detail, "publishedAt", "postedAt"
                )),
                "expires_at": _text(_first(
                    detail, "expiresAt", "expirationDate", "deadline"
                )),
                "external_id": str(job_id) if job_id is not None else None,
                "apply_url": _text(_first(
                    detail, "applyUrl", "applicationUrl"
                )),
            }
            fields.update({key: value for key, value in optional.items()
                           if value is not None})
            fields.update(_salary_fields(detail))
            yield Job(**fields)

        page_count = pagination.get("pageCount", 0)
        if not items or page >= page_count:
            break
        page += 1
