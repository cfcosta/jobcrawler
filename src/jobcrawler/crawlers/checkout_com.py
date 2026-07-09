
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/checkout.com?includeCompensation=true'
EXTRA_HOSTS = []

def _clean_text(value):
    if value is None:
        return None
    text = str(value)
    # Fallback for HTML descriptions if a posting ever lacks descriptionPlain.
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", text)
    text = re.sub(r"(?s)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None

def _set_if_present(out, key, value):
    if value is not None and value != "":
        out[key] = value

def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None
    parts = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        currency = component.get("currencyCode")
        interval = component.get("interval")
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        label = component.get("compensationType") or component.get("label")
        if minimum is None and maximum is None:
            continue
        if minimum is not None and maximum is not None:
            amount = f"{minimum}-{maximum}"
        else:
            amount = str(minimum if minimum is not None else maximum)
        if currency:
            amount = f"{currency} {amount}"
        if interval:
            amount = f"{amount} / {interval}"
        if label:
            amount = f"{label}: {amount}"
        parts.append(amount)
    return "; ".join(parts) if parts else None

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        description = _clean_text(job.get("descriptionPlain") or job.get("descriptionHtml"))
        item = {
            "url": job.get("jobUrl") or SITE,
            "title": job.get("title"),
            "company": "Checkout.com",
            "location": job.get("location"),
            "description": description,
            "source": SITE,
        }
        _set_if_present(item, "external_id", job.get("id"))
        _set_if_present(item, "employment_type", job.get("employmentType"))
        _set_if_present(item, "workplace_type", job.get("workplaceType"))
        _set_if_present(item, "department", job.get("departmentName") or job.get("department") or job.get("team"))
        _set_if_present(item, "posted_at", job.get("publishedAt"))
        _set_if_present(item, "apply_url", job.get("applyUrl"))
        _set_if_present(item, "salary", _salary_from_compensation(job.get("compensation")))
        yield item
