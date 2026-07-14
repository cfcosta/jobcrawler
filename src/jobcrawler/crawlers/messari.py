
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/messari/jobs?content=true"
EXTRA_HOSTS = []

_PAY_URL = SITE + "&pay_transparency=true"


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        value,
    )
    value = re.sub(
        r"(?i)<(?:br|hr)\s*/?>|</?(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr|blockquote)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() or None


def _nonempty(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _names(items):
    names = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = _nonempty(item.get("name"))
        if name and name not in names:
            names.append(name)
    return names


def _compensation(item, output):
    ranges = []
    for value in item.get("pay_input_ranges") or []:
        if not isinstance(value, dict):
            continue
        currency = _nonempty(value.get("currency_type"))
        if (
            value.get("min_cents") is not None
            and value.get("max_cents") is not None
            and currency
        ):
            ranges.append(value)

    currencies = {
        value.get("currency_type").strip()
        for value in ranges
    }
    if not ranges or len(currencies) != 1:
        return

    bounds = {
        (value.get("min_cents"), value.get("max_cents"))
        for value in ranges
    }
    if len(bounds) != 1:
        return

    minimum, maximum = next(iter(bounds))
    output["salary_min"] = minimum / 100
    output["salary_max"] = maximum / 100
    output["salary_currency"] = next(iter(currencies))

    periods = {
        str(value.get("interval")).strip()
        for value in ranges
        if value.get("interval") is not None
        and str(value.get("interval")).strip()
    }
    if len(periods) == 1:
        output["salary_period"] = next(iter(periods))

    printed = {
        _nonempty(value.get("salary"))
        for value in ranges
        if _nonempty(value.get("salary"))
    }
    if len(printed) == 1:
        output["salary"] = next(iter(printed))


def crawl(ctx):
    data = json.loads(ctx.fetch(_PAY_URL))
    for item in data.get("jobs") or []:
        primary_location = _nonempty(
            (item.get("location") or {}).get("name")
        )
        offices = _names(item.get("offices"))
        departments = _names(item.get("departments"))

        output = {
            "url": _nonempty(item.get("absolute_url")),
            "title": _nonempty(item.get("title")),
            "company": "Messari",
            "location": primary_location,
            "description": _text(item.get("content")),
            "source": SITE,
        }

        if offices:
            output["locations"] = offices
        if departments:
            output["department"] = departments[0]
        if item.get("id") is not None:
            output["external_id"] = str(item.get("id"))

        posted_at = _nonempty(item.get("first_published"))
        if posted_at:
            output["posted_at"] = posted_at

        apply_url = _nonempty(item.get("apply_url"))
        if apply_url:
            output["apply_url"] = apply_url

        _compensation(item, output)
        yield {
            key: value
            for key, value in output.items()
            if value is not None
        }
