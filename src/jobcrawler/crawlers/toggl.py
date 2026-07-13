import json
import re

SITE = "https://jobs.ashbyhq.com/toggl"
EXTRA_HOSTS = []


def _app_data(page):
    marker = "window.__appData"
    start = page.find(marker)
    if start < 0:
        raise ValueError("Ashby app data was not found")
    start = page.find("=", start + len(marker))
    if start < 0:
        raise ValueError("Ashby app data assignment was not found")
    payload = page[start + 1:].lstrip()
    data, _ = json.JSONDecoder().raw_decode(payload)
    return data


def _decode_entity(match):
    value = match.group(1)
    named = {
        "amp": "&",
        "apos": "'",
        "gt": ">",
        "hellip": "...",
        "laquo": "<<",
        "ldquo": '"',
        "lsquo": "'",
        "lt": "<",
        "mdash": "-",
        "nbsp": " ",
        "ndash": "-",
        "quot": '"',
        "raquo": ">>",
        "rdquo": '"',
        "rsquo": "'",
    }
    if value in named:
        return named[value]
    try:
        if value.startswith("#x") or value.startswith("#X"):
            return chr(int(value[2:], 16))
        if value.startswith("#"):
            return chr(int(value[1:]))
    except (ValueError, OverflowError):
        pass
    return match.group(0)


def _plain_text(value):
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    value = re.sub(r"(?i)</li\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"&([A-Za-z][A-Za-z0-9]+|#[0-9]+|#[xX][0-9A-Fa-f]+);",
                   _decode_entity, value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _nonempty(value):
    return value if value not in (None, "", [], {}) else None


def _locations(brief):
    values = []
    primary = _nonempty(brief.get("locationName"))
    if primary:
        values.append(primary)
    for item in brief.get("secondaryLocations") or []:
        if isinstance(item, dict):
            name = _nonempty(
                item.get("locationName") or item.get("locationExternalName")
            )
            if name:
                values.append(name)
    return values


def _compensation(posting):
    result = {}
    printed = _nonempty(
        posting.get("scrapeableCompensationSalarySummary")
        or posting.get("compensationTierSummary")
    )
    if isinstance(printed, str):
        result["salary"] = printed

    components = []

    def visit(value):
        if isinstance(value, dict):
            if (
                value.get("currencyCode")
                and value.get("interval")
                and (
                    value.get("minValue") is not None
                    or value.get("maxValue") is not None
                )
            ):
                components.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(posting.get("compensationTiers") or [])
    currencies = {item.get("currencyCode") for item in components}
    periods = {item.get("interval") for item in components}
    if components and len(currencies) == 1 and len(periods) == 1:
        mins = [
            item["minValue"] for item in components
            if isinstance(item.get("minValue"), (int, float))
        ]
        maxes = [
            item["maxValue"] for item in components
            if isinstance(item.get("maxValue"), (int, float))
        ]
        if mins:
            result["salary_min"] = min(mins)
        if maxes:
            result["salary_max"] = max(maxes)
        result["salary_currency"] = next(iter(currencies))
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    board_page = ctx.fetch(SITE)
    board = _app_data(board_page).get("jobBoard") or {}
    seen = set()

    for brief in board.get("jobPostings") or []:
        posting_id = _nonempty(brief.get("id"))
        if not posting_id or posting_id in seen or brief.get("isListed") is False:
            continue
        seen.add(posting_id)

        url = SITE.rstrip("/") + "/" + posting_id
        detail = _app_data(ctx.fetch(url)).get("posting") or {}
        if detail.get("isListed") is False:
            continue

        description = _plain_text(detail.get("descriptionHtml"))
        title = _nonempty(detail.get("title") or brief.get("title"))
        location = _nonempty(
            detail.get("locationName") or brief.get("locationName")
        )
        if not title or not location or not description:
            continue

        values = {
            "url": url,
            "title": title,
            "company": "Toggl",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting_id,
            "apply_url": url,
        }

        locations = _locations(brief)
        if locations:
            values["locations"] = locations

        optional = {
            "employment_type": detail.get("employmentType")
                or brief.get("employmentType"),
            "workplace_type": detail.get("workplaceType")
                or brief.get("workplaceType"),
            "department": detail.get("departmentExternalName")
                or detail.get("departmentName")
                or brief.get("departmentExternalName")
                or brief.get("departmentName"),
            "posted_at": brief.get("publishedDate"),
            "expires_at": detail.get("applicationDeadline")
                or brief.get("applicationDeadline"),
        }
        for key, value in optional.items():
            value = _nonempty(value)
            if value is not None:
                values[key] = value

        values.update(_compensation(detail))
        yield values
