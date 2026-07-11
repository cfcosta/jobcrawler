
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/chief?includeCompensation=true"
EXTRA_HOSTS = []


def _text_from_response(resp):
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)


def _strip_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _locations(job):
    vals = []
    primary = job.get("location")
    if primary:
        vals.append(primary)
    for loc in job.get("secondaryLocations") or []:
        if isinstance(loc, dict):
            name = loc.get("location") or loc.get("name")
        else:
            name = loc
        if name and name not in vals:
            vals.append(name)
    return vals


def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if printed:
        out["salary"] = printed

    components = comp.get("summaryComponents") or []
    mins, maxes, currencies, periods = [], [], set(), set()
    for component in components:
        if not isinstance(component, dict):
            continue
        currency = component.get("currencyCode")
        if currency:
            currencies.add(currency)
        interval = component.get("interval")
        if interval:
            periods.add(interval)
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        if min_value is not None:
            mins.append(min_value)
        if max_value is not None:
            maxes.append(max_value)

    if len(currencies) == 1 and (mins or maxes):
        if mins:
            out["salary_min"] = min(mins)
        if maxes:
            out["salary_max"] = max(maxes)
        out["salary_currency"] = next(iter(currencies))
        if len(periods) == 1:
            out["salary_period"] = next(iter(periods))


def crawl(ctx):
    raw = _text_from_response(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        title = job.get("title")
        url = job.get("jobUrl")
        location = job.get("location")
        description = job.get("descriptionPlain") or _strip_html(job.get("descriptionHtml"))

        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Chief",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        if job.get("id"):
            out["external_id"] = job.get("id")
        if job.get("applyUrl"):
            out["apply_url"] = job.get("applyUrl")
        if job.get("employmentType"):
            out["employment_type"] = job.get("employmentType")
        if job.get("workplaceType"):
            out["workplace_type"] = job.get("workplaceType")
        elif job.get("isRemote") is True:
            out["workplace_type"] = "Remote"
        if job.get("department"):
            out["department"] = job.get("department")
        elif job.get("team"):
            out["department"] = job.get("team")
        if job.get("publishedAt"):
            out["posted_at"] = job.get("publishedAt")

        _add_compensation(out, job)
        yield out
