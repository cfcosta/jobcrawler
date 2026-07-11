
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/phantom?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _html_to_text(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    replacements = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _clean(text)


def _locations(job):
    values = []
    primary = _clean(job.get("location"))
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            loc = _clean(item.get("location") or item.get("name"))
        else:
            loc = _clean(item)
        if loc and loc not in values:
            values.append(loc)
    return values


def _compensation_fields(job):
    comp = job.get("compensation") or {}
    fields = {}
    printed = _clean(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"))
    if printed:
        fields["salary"] = printed

    usable = []
    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if (min_value is not None or max_value is not None) and currency:
            usable.append((min_value, max_value, currency, interval))

    currencies = set([u[2] for u in usable])
    if len(currencies) == 1 and usable:
        mins = [u[0] for u in usable if u[0] is not None]
        maxs = [u[1] for u in usable if u[1] is not None]
        periods = [u[3] for u in usable if u[3]]
        if mins:
            fields["salary_min"] = min(mins)
        if maxs:
            fields["salary_max"] = max(maxs)
        fields["salary_currency"] = usable[0][2]
        if periods:
            fields["salary_period"] = periods[0]
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Phantom",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        optional_map = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType"),
            "department": job.get("department"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        for key, value in optional_map.items():
            value = _clean(value)
            if value is not None:
                item[key] = value

        item.update(_compensation_fields(job))
        yield item
