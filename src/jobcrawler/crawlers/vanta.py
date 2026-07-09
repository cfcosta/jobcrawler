
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/vanta?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    value = value.strip()
    return value or None


def _salary(compensation):
    if not isinstance(compensation, dict):
        return None

    parts = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        comp_type = component.get("compensationType")
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        label = comp_type or "Compensation"
        if min_value is not None and max_value is not None:
            amount = f"{min_value:g} - {max_value:g}"
        else:
            amount = f"{(min_value if min_value is not None else max_value):g}"
        bits = [label, amount]
        if currency:
            bits.append(currency)
        if interval:
            bits.append(interval)
        parts.append(" ".join(bits))

    if parts:
        return "; ".join(parts)

    return _clean(
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Vanta",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "department": _clean(job.get("department") or job.get("departmentName") or job.get("team")),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _salary(job.get("compensation")),
        }
        for key, value in optional_fields.items():
            if value is not None:
                item[key] = value

        yield item
