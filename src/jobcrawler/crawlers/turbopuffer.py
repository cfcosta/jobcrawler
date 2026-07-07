
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/turbopuffer?includeCompensation=true'
EXTRA_HOSTS = []

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"<br\\s*/?>", "\n", value)
    text = re.sub(r"</p\\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\\n{3,}", "\n\n", text)
    return text.strip() or None

def _set_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value

def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None
    summary = compensation.get("scrapeableCompensationSalarySummary")
    if summary:
        return summary
    pieces = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        label = component.get("label")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value}-{max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)
        bits = [currency, amount]
        if interval:
            bits.append(f"per {interval}")
        if label:
            bits.append(f"({label})")
        pieces.append(" ".join(str(bit) for bit in bits if bit))
    return "; ".join(pieces) if pieces else None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        job = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "turbopuffer",
            "location": item.get("location"),
            "description": description,
            "source": SITE,
        }

        _set_if_present(job, "external_id", item.get("id"))
        _set_if_present(job, "apply_url", item.get("applyUrl"))
        _set_if_present(job, "department", item.get("department") or item.get("team"))
        _set_if_present(job, "employment_type", item.get("employmentType"))
        _set_if_present(job, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") else None))
        _set_if_present(job, "posted_at", item.get("publishedAt"))
        _set_if_present(job, "salary", _salary_from_compensation(item.get("compensation")))

        yield job
