
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/oplabs?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _html_to_text(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return _clean(text)


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None

    summary = _clean(compensation.get("scrapeableCompensationSalarySummary"))
    if summary:
        return summary

    pieces = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None:
            amount = str(min_value) + "-" + str(max_value)
        elif min_value is not None:
            amount = str(min_value)
        else:
            amount = str(max_value)
        if currency:
            amount = currency + " " + amount
        if interval:
            amount = amount + " / " + interval
        pieces.append(amount)

    if pieces:
        return "; ".join(pieces)
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        if not description:
            description = _html_to_text(job.get("descriptionHtml"))

        item = {
            "url": _clean(job.get("jobUrl")) or SITE,
            "title": _clean(job.get("title")),
            "company": "OP Labs",
            "location": _clean(job.get("location")),
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "department": _clean(job.get("team")) or _clean(job.get("department")),
            "posted_at": _clean(job.get("publishedAt")),
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "salary": _salary_from_compensation(job.get("compensation")),
        }
        for key, value in optional_fields.items():
            if value:
                item[key] = value

        yield item
