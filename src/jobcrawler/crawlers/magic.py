import html
import json


SITE = "https://boards-api.greenhouse.io/v1/boards/magic/jobs?content=true"
EXTRA_HOSTS = []
FETCH_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    text = html.unescape(value or "")
    output = []
    in_tag = False

    for char in text:
        if char == "<":
            in_tag = True
            if output and output[-1] != "\n":
                output.append("\n")
        elif char == ">":
            in_tag = False
        elif not in_tag:
            output.append(char)

    lines = []
    for line in "".join(output).splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name and name not in names:
                names.append(name)
    return names


def _compensation(item):
    ranges = item.get("pay_input_ranges") or []
    usable = []
    for pay_range in ranges:
        if not isinstance(pay_range, dict):
            continue
        if not pay_range.get("currency_type"):
            continue
        if pay_range.get("min_cents") is None and pay_range.get("max_cents") is None:
            continue
        usable.append(pay_range)

    currencies = set()
    for pay_range in usable:
        currencies.add(pay_range["currency_type"])
    if not usable or len(currencies) != 1:
        return {}

    result = {"salary_currency": next(iter(currencies))}
    minimums = []
    maximums = []
    for pay_range in usable:
        if pay_range.get("min_cents") is not None:
            minimums.append(pay_range["min_cents"] / 100)
        if pay_range.get("max_cents") is not None:
            maximums.append(pay_range["max_cents"] / 100)
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    return result


def crawl(ctx):
    response = ctx.fetch(FETCH_URL)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location_data = item.get("location") or {}
        fields = {
            "source": SITE,
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Magic",
            "location": location_data.get("name"),
            "description": _plain_text(item.get("content")),
        }

        if item.get("id") is not None:
            fields["external_id"] = str(item["id"])

        offices = _names(item.get("offices"))
        if offices:
            fields["locations"] = offices

        departments = _names(item.get("departments"))
        if departments:
            fields["department"] = ", ".join(departments)

        fields.update(_compensation(item))
        yield {key: value for key, value in fields.items() if value is not None}
