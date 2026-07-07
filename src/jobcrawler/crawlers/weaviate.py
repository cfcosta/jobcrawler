
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/weaviate?includeCompensation=true"


def _html_to_text(html):
    if not html:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _salary_from_compensation(comp):
    if not isinstance(comp, dict):
        return None

    text_summary = _clean(comp.get("compensationTierSummary")) or _clean(comp.get("scrapeableCompensationSalarySummary"))
    if text_summary:
        return text_summary

    pieces = []
    for item in comp.get("summaryComponents") or []:
        if not isinstance(item, dict):
            continue
        currency = _clean(item.get("currencyCode"))
        interval = _clean(item.get("interval"))
        min_value = item.get("minValue")
        max_value = item.get("maxValue")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = str(min_value) + "-" + str(max_value)
        else:
            amount = str(min_value if min_value is not None else max_value)
        bits = [b for b in (currency, amount, interval) if b]
        if bits:
            pieces.append(" ".join(bits))
    return "; ".join(pieces) or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))

        item = {
            "url": _clean(job.get("jobUrl")),
            "title": _clean(job.get("title")),
            "company": "Weaviate",
            "location": _clean(job.get("location")),
            "description": description,
            "source": SITE,
        }

        optional = {
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "department": _clean(job.get("department")) or _clean(job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _salary_from_compensation(job.get("compensation")),
        }
        for key, value in optional.items():
            if value is not None:
                item[key] = value

        if item["url"] and item["title"] and item["company"] and item["location"] and item["description"]:
            yield item
