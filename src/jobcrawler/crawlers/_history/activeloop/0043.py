import json

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

COMPANY = "Activeloop"
APPLY_BASE = "https://app.dover.com/apply/Activeloop"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job"

_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "div", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "ol", "p", "pre", "section", "table", "tr", "ul",
}

_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "hellip": "...",
    "ldquo": '"',
    "lsquo": "'",
    "lt": "<",
    "mdash": "-",
    "nbsp": " ",
    "ndash": "-",
    "quot": '"',
    "rdquo": '"',
    "rsquo": "'",
}


def _decode_entity(value):
    if value in _ENTITIES:
        return _ENTITIES[value]
    try:
        if value.startswith("#x") or value.startswith("#X"):
            return chr(int(value[2:], 16))
        if value.startswith("#"):
            return chr(int(value[1:]))
    except ValueError:
        pass
    return "&" + value + ";"


def _decode_entities(value):
    output = []
    position = 0
    while position < len(value):
        if value[position] != "&":
            output.append(value[position])
            position += 1
            continue

        end = value.find(";", position + 1, position + 16)
        if end < 0:
            output.append("&")
            position += 1
            continue

        output.append(_decode_entity(value[position + 1:end]))
        position = end + 1
    return "".join(output)


def _html_to_text(value):
    output = []
    position = 0
    hidden_tag = None

    while position < len(value or ""):
        if value[position] != "<":
            if hidden_tag is None:
                output.append(value[position])
            position += 1
            continue

        end = value.find(">", position + 1)
        if end < 0:
            if hidden_tag is None:
                output.append(value[position:])
            break

        raw_tag = value[position + 1:end].strip()
        closing = raw_tag.startswith("/")
        tag = raw_tag.lstrip("/").split(None, 1)[0].rstrip("/").lower()

        if tag in {"script", "style"}:
            hidden_tag = None if closing else tag
        elif hidden_tag is None and tag in _BLOCK_TAGS:
            output.append("\n")

        position = end + 1

    decoded = _decode_entities("".join(output))
    lines = []
    for line in decoded.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    response = ctx.fetch(SITE + "?format=json")
    groups = json.loads(response)

    for group in groups:
        department = group.get("name")
        if department == "Ungrouped":
            department = None

        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True or listing.get("is_sample") is True:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail_response = ctx.fetch(DETAIL_BASE + "/" + job_id)
            detail = json.loads(detail_response)
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description") or "")
            if not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = _location_name(item)
                if not name:
                    continue
                locations.append(name)
                if item.get("is_primary"):
                    primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = APPLY_BASE + "/" + job_id + "/"
            values = {
                "url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": COMPANY,
                "location": location,
                "locations": locations or None,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": detail.get("workplace_type") or listing.get("workplace_type"),
                "department": department,
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            values["employment_type"] = compensation.get("employment_type")
            values["salary_min"] = compensation.get("lower_bound")
            values["salary_max"] = compensation.get("upper_bound")
            values["salary_currency"] = compensation.get("currency_code")
            values["salary_period"] = compensation.get("salary_range_type")

            yield Job(**{key: value for key, value in values.items() if value is not None})
