
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/0x?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_text(value):
    if value is None:
        return None
    text = str(value)
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None

    parts = []

    summary = compensation.get("scrapeableCompensationSalarySummary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    tier_summary = compensation.get("compensationTierSummary")
    if isinstance(tier_summary, str) and tier_summary.strip():
        return tier_summary.strip()

    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")

        if min_value is None and max_value is None:
            continue

        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value}-{max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)

        if currency:
            amount = f"{currency} {amount}"
        if interval:
            amount = f"{amount} / {interval}"
        parts.append(amount)

    return "; ".join(parts) or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    for job in data.get("jobs") or []:
        if not isinstance(job, dict) or job.get("isListed") is False:
            continue

        description = _clean_text(job.get("descriptionPlain")) or _clean_text(job.get("descriptionHtml"))
        title = _clean_text(job.get("title"))
        url = job.get("jobUrl")
        location = _clean_text(job.get("location"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "0x",
            "location": location,
            "description": description,
            "source": SITE,
        }

        field_map = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType"),
            "department": job.get("department") or job.get("departmentName") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        for key, value in field_map.items():
            value = _clean_text(value)
            if value:
                item[key] = value

        salary = _salary_from_compensation(job.get("compensation"))
        if salary:
            item["salary"] = salary

        yield item
