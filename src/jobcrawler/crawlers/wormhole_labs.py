
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/wormholelabs?includeCompensation=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = value.strip()
    return value or None


def _html_to_text(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _employment_type(value):
    value = _clean(value)
    if not value:
        return None
    return re.sub(r"(?<!^)([A-Z])", r" \1", value).strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        desc = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))

        item = {
            "url": _clean(job.get("jobUrl")),
            "title": _clean(job.get("title")),
            "company": "Wormhole Labs",
            "location": _clean(job.get("location")),
            "description": desc,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "department": _clean(job.get("department") or job.get("team")),
            "employment_type": _employment_type(job.get("employmentType")),
            "posted_at": _clean(job.get("publishedAt")),
        }

        workplace_type = _clean(job.get("workplaceType"))
        if workplace_type:
            item["workplace_type"] = workplace_type

        apply_url = _clean(job.get("applyUrl") or job.get("applicationUrl"))
        if apply_url:
            item["apply_url"] = apply_url

        comps = ((job.get("compensation") or {}).get("summaryComponents") or [])
        for comp in comps:
            if not isinstance(comp, dict):
                continue
            min_value = comp.get("minValue")
            max_value = comp.get("maxValue")
            currency = _clean(comp.get("currencyCode"))
            interval = _clean(comp.get("interval"))
            if min_value is not None or max_value is not None or currency or interval:
                salary = {}
                if min_value is not None:
                    salary["min"] = min_value
                if max_value is not None:
                    salary["max"] = max_value
                if currency:
                    salary["currency"] = currency
                if interval:
                    salary["interval"] = interval
                item["salary"] = salary
                break

        yield {k: v for k, v in item.items() if v is not None}
