
import json
from urllib.parse import urlencode

SITE = "https://join.com/api/public/companies/81923/jobs?page=1&pageSize=5"
EXTRA_HOSTS = []

_COMPANY_ID = 81923
_COMPANY = "Memgraph"
_COMPANY_SLUG = "memgraph"
_PAGE_SIZE = 5


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _location(item, detail):
    city = item.get("city") or detail.get("city") or {}
    parts = []
    for key in ("cityName", "regionName", "countryName"):
        value = _text(city.get(key))
        if value and value not in parts:
            parts.append(value)
    if parts:
        return ", ".join(parts)

    remote_region = detail.get("remoteRegion") or {}
    return (
        _text(remote_region.get("name"))
        or _text((detail.get("country") or {}).get("name"))
        or _text((item.get("country") or {}).get("name"))
        or _text(detail.get("workplaceType"))
        or _text(item.get("workplaceType"))
    )


def _salary_fields(item, detail):
    if not (detail.get("settings") or item.get("settings") or {}).get("showSalary"):
        return {}

    low = detail.get("salaryAmountFrom") or item.get("salaryAmountFrom")
    high = detail.get("salaryAmountTo") or item.get("salaryAmountTo")
    fields = {}

    def amount(value):
        if isinstance(value, dict):
            return value.get("amount")
        return value

    low_value = amount(low)
    high_value = amount(high)
    if low_value is not None:
        fields["salary_min"] = low_value
    if high_value is not None:
        fields["salary_max"] = high_value

    currencies = {
        value.get("currency")
        for value in (low, high)
        if isinstance(value, dict) and value.get("currency")
    }
    if len(currencies) == 1:
        fields["salary_currency"] = currencies.pop()

    frequency = _text(detail.get("salaryFrequency") or item.get("salaryFrequency"))
    if frequency:
        fields["salary_period"] = frequency
    return fields


def crawl(ctx):
    page = 1
    page_count = 1

    while page <= page_count:
        query = urlencode({"page": page, "pageSize": _PAGE_SIZE})
        listing_url = (
            f"https://join.com/api/public/companies/{_COMPANY_ID}/jobs?{query}"
        )
        payload = json.loads(ctx.fetch(listing_url))
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 0)

        for item in payload.get("items") or []:
            detail_key = item.get("idParam") or item.get("id")
            if detail_key is None:
                continue
            detail = json.loads(
                ctx.fetch(f"https://join.com/api/public/jobs/{detail_key}")
            )
            description = _text(
                detail.get("description") or detail.get("descriptionHtml")
            )
            title = _text(detail.get("title") or item.get("title"))
            if not title or not description:
                continue

            id_param = _text(detail.get("idParam") or item.get("idParam"))
            if not id_param:
                id_param = str(detail.get("id") or item.get("id"))
            canonical_url = (
                f"https://join.com/companies/{_COMPANY_SLUG}/{id_param}"
            )

            kwargs = {
                "url": canonical_url,
                "title": title,
                "company": _COMPANY,
                "location": _location(item, detail),
                "description": description,
                "source": "join.com",
                "external_id": str(detail.get("id") or item.get("id")),
            }

            optional = {
                "department": _text(
                    (detail.get("category") or item.get("category") or {}).get("name")
                ),
                "employment_type": _text(
                    (
                        detail.get("employmentType")
                        or item.get("employmentType")
                        or {}
                    ).get("name")
                ),
                "workplace_type": _text(
                    detail.get("workplaceType") or item.get("workplaceType")
                ),
                "posted_at": _text(
                    detail.get("createdAt") or item.get("createdAt")
                ),
                "apply_url": canonical_url,
            }
            kwargs.update({key: value for key, value in optional.items() if value})
            kwargs.update(_salary_fields(item, detail))
            yield kwargs

        page += 1
