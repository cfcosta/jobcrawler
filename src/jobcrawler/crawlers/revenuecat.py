
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/revenuecat?includeCompensation=true"
EXTRA_HOSTS = []

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None

def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None
    pieces = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        currency = component.get("currencyCode")
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        interval = component.get("interval")
        if not currency or min_value is None:
            continue
        if max_value is not None and max_value != min_value:
            amount = "%s %s-%s" % (currency, min_value, max_value)
        else:
            amount = "%s %s" % (currency, min_value)
        if interval:
            amount = amount + " per " + str(interval).lower()
        pieces.append(amount)
    return "; ".join(pieces) or None

def _set_if_present(target, key, value):
    if value is not None and value != "":
        target[key] = value

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        url = job.get("jobUrl")
        title = job.get("title")
        location = job.get("location")

        if not (url and title and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "RevenueCat",
            "location": location,
            "description": description,
            "source": SITE,
        }

        _set_if_present(item, "external_id", job.get("id"))
        _set_if_present(item, "employment_type", job.get("employmentType"))
        _set_if_present(item, "workplace_type", job.get("workplaceType"))
        _set_if_present(item, "department", job.get("departmentName") or job.get("department") or job.get("team"))
        _set_if_present(item, "posted_at", job.get("publishedAt"))
        _set_if_present(item, "apply_url", job.get("jobUrl"))
        _set_if_present(item, "salary", _salary_from_compensation(job.get("compensation")))

        yield item
