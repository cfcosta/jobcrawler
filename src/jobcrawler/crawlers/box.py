
import html
import json
import re
from jobcrawler.runtime.contract import Job
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SITE = "https://boards-api.greenhouse.io/v1/boards/boxinc/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.replace("\r", "\n").splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _api_url():
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["content"] = "true"
    query["pay_transparency"] = "true"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _response_json(response):
    body = response.text if hasattr(response, "text") else response
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name:
            value = item.get("value")
            if value not in (None, ""):
                return value
    return None


def crawl(ctx):
    payload = _response_json(ctx.fetch(_api_url()))

    for item in payload.get("jobs") or []:
        url = item.get("absolute_url")
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content"))
        if not url or not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Box, Inc.",
            "location": location,
            "description": description,
            "source": "Greenhouse",
            "external_id": (
                item.get("requisition_id")
                or (str(item["id"]) if item.get("id") is not None else None)
            ),
            "employment_type": _metadata_value(item, "Employment Type"),
            "posted_at": item.get("first_published"),
        }

        locations = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name") not in (None, "")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name") not in (None, "")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield Job(**{key: value for key, value in fields.items() if value is not None})
