import html
import json
import re

SITE = (
    "https://api.ashbyhq.com/posting-api/job-board/nethermind?includeCompensation=true"
)
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _location(job):
    pieces = []
    primary = _clean(job.get("location"))
    if primary:
        pieces.append(primary)
    for loc in job.get("secondaryLocations") or []:
        if isinstance(loc, str):
            name = _clean(loc)
        elif isinstance(loc, dict):
            name = _clean(loc.get("location") or loc.get("name"))
        else:
            name = None
        if name and name not in pieces:
            pieces.append(name)
    if job.get("isRemote") and "Remote" not in pieces:
        pieces.insert(0, "Remote")
    return ", ".join(pieces) if pieces else None


def _salary(job):
    comp = job.get("compensation") or {}
    parts = []
    summary = _clean(
        comp.get("scrapeableCompensationSalarySummary")
        or comp.get("compensationTierSummary")
    )
    if summary:
        parts.append(summary)

    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None:
            text = "%s-%s" % (min_value, max_value)
        else:
            text = str(min_value if min_value is not None else max_value)
        if currency:
            text = currency + " " + text
        if interval:
            text += " " + interval
        parts.append(text)

    if not parts:
        return None
    return "; ".join(parts)


def _job_url(job):
    return _clean(job.get("jobUrl")) or (
        "https://jobs.ashbyhq.com/nethermind/" + job.get("id", "")
    )


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for job in payload.get("jobs", []):
        if not job.get("isListed", True):
            continue

        description = _clean(job.get("descriptionPlain")) or _text_from_html(
            job.get("descriptionHtml")
        )
        item = {
            "source": SITE,
            "url": _job_url(job),
            "title": _clean(job.get("title")),
            "company": "Nethermind",
            "location": _location(job),
            "description": description,
            "external_id": _clean(job.get("id")),
            "department": _clean(job.get("department") or job.get("team")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType"))
            or ("Remote" if job.get("isRemote") else None),
            "posted_at": _clean(job.get("publishedAt")),
            "apply_url": _job_url(job),
        }

        salary = _salary(job)
        if salary:
            item["salary"] = salary

        yield {key: value for key, value in item.items() if value not in (None, "")}
