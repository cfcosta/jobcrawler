
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/flashbots.net?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location(job):
    loc = job.get("location")
    if isinstance(loc, dict):
        loc = loc.get("name") or loc.get("location") or loc.get("address")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    secondary = job.get("secondaryLocations") or []
    names = []
    for item in secondary:
        if isinstance(item, dict):
            name = item.get("name") or item.get("location")
        else:
            name = item
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    if names:
        return ", ".join(names)
    if job.get("workplaceType") or job.get("isRemote") is True:
        return "Remote"
    return None


def _compensation(job):
    comp = job.get("compensation") or {}
    pieces = []
    for item in comp.get("summaryComponents") or []:
        if not isinstance(item, dict):
            continue
        min_value = item.get("minValue")
        max_value = item.get("maxValue")
        currency = item.get("currencyCode")
        interval = item.get("interval")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value} - {max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)
        label = " ".join(str(x) for x in [currency, amount, interval] if x)
        if label:
            pieces.append(label)
    return "; ".join(pieces) or None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue
        title = (job.get("title") or "").strip()
        description = (job.get("descriptionPlain") or "").strip() or _clean_html(job.get("descriptionHtml"))
        url = (job.get("jobUrl") or job.get("applyUrl") or "").strip()
        location = _location(job)
        if not (title and description and url and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Flashbots",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "employment_type": job.get("employmentType"),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
        }

        workplace = job.get("workplaceType")
        if workplace:
            item["workplace_type"] = workplace
        elif job.get("isRemote") is True:
            item["workplace_type"] = "Remote"

        salary = _compensation(job)
        if salary:
            item["salary"] = salary

        yield {k: v for k, v in item.items() if v is not None and v != ""}
