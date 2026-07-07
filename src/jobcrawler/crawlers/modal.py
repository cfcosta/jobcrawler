
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/modal?includeCompensation=true"


def _html_to_text(html):
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html or "", flags=re.I)
    text = re.sub(r"</\s*(p|div|li|h1|h2|h3|h4|tr|ul|ol)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _location(job):
    locs = []
    first = _clean(job.get("location"))
    if first:
        locs.append(first)
    for item in job.get("secondaryLocations") or []:
        loc = _clean(item.get("location") if isinstance(item, dict) else item)
        if loc and loc not in locs:
            locs.append(loc)
    return "; ".join(locs) or None


def _salary(compensation):
    if not isinstance(compensation, dict):
        return None
    for comp in compensation.get("summaryComponents") or []:
        if not isinstance(comp, dict):
            continue
        ctype = comp.get("compensationType") or ""
        if "Salary" not in ctype:
            continue
        currency = _clean(comp.get("currencyCode"))
        min_value = comp.get("minValue")
        max_value = comp.get("maxValue")
        interval = _clean(comp.get("interval"))
        if min_value is not None and max_value is not None:
            parts = []
            if currency:
                parts.append(currency)
            parts.append(str(min_value))
            parts.append("-")
            parts.append(str(max_value))
            if interval:
                parts.append(interval)
            return " ".join(parts)
    return _clean(compensation.get("scrapeableCompensationSalarySummary")) or _clean(compensation.get("compensationTierSummary"))


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        location = _location(job)
        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Modal",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "department": _clean(job.get("department") or job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _salary(job.get("compensation")),
        }
        for key, value in optional.items():
            if value is not None:
                item[key] = value

        yield item
