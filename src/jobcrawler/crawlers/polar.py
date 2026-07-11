
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/polar?includeCompensation=true"
EXTRA_HOSTS = []


def _response_text(resp):
    if isinstance(resp, str):
        return resp
    return resp.text


def _html_to_text(value):
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", value or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&nbsp;": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = None
        if isinstance(item, dict):
            loc = item.get("location")
        else:
            loc = item
        if loc:
            values.append(loc)

    deduped = []
    for loc in values:
        if loc not in deduped:
            deduped.append(loc)
    return deduped


def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if printed:
        out["salary"] = printed

    components = comp.get("summaryComponents") or []
    currencies = []
    for component in components:
        currency = component.get("currencyCode")
        if currency and currency not in currencies:
            currencies.append(currency)
    if len(currencies) != 1:
        return

    mins = []
    maxs = []
    periods = []
    for component in components:
        if component.get("minValue") is not None:
            mins.append(component.get("minValue"))
        if component.get("maxValue") is not None:
            maxs.append(component.get("maxValue"))
        if component.get("interval"):
            periods.append(component.get("interval"))

    if mins:
        out["salary_min"] = min(mins)
    if maxs:
        out["salary_max"] = max(maxs)
    if mins or maxs:
        out["salary_currency"] = currencies[0]
        if periods:
            out["salary_period"] = periods[0]


def crawl(ctx):
    data = json.loads(_response_text(ctx.fetch(SITE)))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _html_to_text(job.get("descriptionHtml"))
        locs = _locations(job)
        out = {
            "url": job.get("jobUrl") or SITE,
            "title": job.get("title"),
            "company": "Polar",
            "location": job.get("location") or (locs[0] if locs else None),
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
        }
        if locs:
            out["locations"] = locs
        _add_compensation(out, job)

        clean = {}
        for key, value in out.items():
            if value is not None and value != "":
                clean[key] = value
        yield clean
