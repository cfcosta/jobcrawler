import json

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _entity(value):
    named = {
        "amp": "&",
        "apos": "'",
        "gt": ">",
        "hellip": "...",
        "lt": "<",
        "nbsp": " ",
        "quot": '"',
        "rsquo": "'",
        "lsquo": "'",
        "rdquo": '"',
        "ldquo": '"',
        "ndash": "-",
        "mdash": "--",
        "bull": "-",
    }
    if value in named:
        return named[value]
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except ValueError:
            return ""
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return ""
    return f"&{value};"


def _text(value):
    source = value or ""
    output = []
    tag = []
    entity = []
    in_tag = False
    in_entity = False
    block_tags = {
        "address", "article", "aside", "blockquote", "br", "div", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }

    for char in source:
        if in_tag:
            if char == ">":
                name = "".join(tag).strip().lower()
                if name.startswith("/"):
                    name = name[1:].strip()
                name = name.split(" ", 1)[0].rstrip("/")
                if name in block_tags:
                    output.append("\n")
                tag = []
                in_tag = False
            else:
                tag.append(char)
        elif in_entity:
            if char == ";":
                output.append(_entity("".join(entity)))
                entity = []
                in_entity = False
            elif char.isspace() or len(entity) > 12:
                output.append("&")
                output.extend(entity)
                output.append(char)
                entity = []
                in_entity = False
            else:
                entity.append(char)
        elif char == "<":
            in_tag = True
            tag = []
        elif char == "&":
            in_entity = True
            entity = []
        else:
            output.append(char)

    if in_entity:
        output.append("&")
        output.extend(entity)

    lines = []
    for line in "".join(output).splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    page_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        response = ctx.fetch(page_url)
        page = json.loads(response)

        for listed in page.get("results") or []:
            job_id = listed.get("id")
            if not job_id:
                continue
            if job_id in seen_jobs:
                continue
            if not listed.get("is_published") or listed.get("is_sample"):
                continue
            seen_jobs.add(job_id)

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            detail_url = f"https://app.dover.com/api/v1/inbound/application-portal-job/{job_id}"
            detail_response = ctx.fetch(detail_url)
            detail = json.loads(detail_response)

            if not detail.get("active") or detail.get("is_private"):
                continue

            title = detail.get("title") or listed.get("title")
            description = _text(detail.get("user_provided_description"))
            location_items = detail.get("locations") or listed.get("locations") or []
            locations = []
            primary = None
            for item in location_items:
                name = item.get("name")
                if name:
                    locations.append(name)
                    if item.get("is_primary"):
                        primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not title or not description or not location:
                continue

            data = {
                "title": title,
                "company": detail.get("client_name") or "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
            }
            if locations:
                data["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                data["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                data["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                data["posted_at"] = created

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            currency = compensation.get("currency_code")
            period = compensation.get("salary_range_type")
            if lower is not None and upper is not None and currency and period:
                data["salary_min"] = lower
                data["salary_max"] = upper
                data["salary_currency"] = currency
                data["salary_period"] = period

            yield Job(url=apply_url, **data)

        next_url = page.get("next")
        if next_url and next_url.startswith("/"):
            page_url = f"https://app.dover.com{next_url}"
        else:
            page_url = next_url
