import html
import json
import re

SITE = "https://api.rippling.com/platform/api/ats/v1/board/agora/jobs"
EXTRA_HOSTS = []


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _text(value):
    if not value:
        return None

    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:p|div|section|h[1-6]|ul|ol|li|table|tr)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def _label(value):
    if isinstance(value, dict):
        return value.get("id") or value.get("label")
    return value


def crawl(ctx):
    rows = _json(ctx.fetch(SITE))
    seen = set()

    for row in rows:
        job_id = row.get("uuid")
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)

        detail = _json(ctx.fetch(SITE.rstrip("/") + "/" + job_id))
        body = detail.get("description") or {}
        description = "\n\n".join(
            part
            for part in (_text(body.get("company")), _text(body.get("role")))
            if part
        )

        locations = [
            location.strip()
            for location in (detail.get("workLocations") or [])
            if isinstance(location, str) and location.strip()
        ]
        if not locations:
            listing_location = (row.get("workLocation") or {}).get("label")
            if listing_location:
                locations = [listing_location]

        url = detail.get("url") or row.get("url")
        fields = {
            "url": url,
            "title": (detail.get("name") or row.get("name") or "").strip(),
            "company": "Agora",
            "location": locations[0] if locations else None,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "external_id": job_id,
            "apply_url": url,
            "department": _label(
                detail.get("department") or row.get("department")
            ),
            "employment_type": _label(detail.get("employmentType")),
            "posted_at": detail.get("createdOn"),
        }

        pay_ranges = detail.get("payRangeDetails") or []
        currencies = {
            pay.get("currency") for pay in pay_ranges if pay.get("currency")
        }
        frequencies = {
            pay.get("frequency") for pay in pay_ranges if pay.get("frequency")
        }

        if pay_ranges and len(currencies) == 1 and len(frequencies) == 1:
            minimums = [
                pay["rangeStart"]
                for pay in pay_ranges
                if pay.get("rangeStart") is not None
            ]
            maximums = [
                pay["rangeEnd"]
                for pay in pay_ranges
                if pay.get("rangeEnd") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))
            fields["salary_period"] = next(iter(frequencies))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
