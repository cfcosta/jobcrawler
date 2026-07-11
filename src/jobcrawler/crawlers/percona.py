
import json
import re
import html

SITE = 'https://api.ashbyhq.com/posting-api/job-board/percona?includeCompensation=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", value)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \\t\\r\\f\\v]+", " ", text)
    text = re.sub(r"\\n\\s*\\n\\s*\\n+", "\n\n", text)
    return text.strip() or None


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)
    for loc in job.get("secondaryLocations") or []:
        name = loc.get("location") if isinstance(loc, dict) else None
        if name:
            values.append(name)

    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _compensation_fields(job):
    comp = job.get("compensation") or {}
    fields = {}

    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if printed:
        fields["salary"] = printed

    components = comp.get("summaryComponents") or []
    currencies = {c.get("currencyCode") for c in components if c.get("currencyCode")}
    if len(currencies) == 1:
        mins = [c.get("minValue") for c in components if c.get("minValue") is not None]
        maxs = [c.get("maxValue") for c in components if c.get("maxValue") is not None]
        periods = [c.get("interval") for c in components if c.get("interval")]
        if mins:
            fields["salary_min"] = min(mins)
        if maxs:
            fields["salary_max"] = max(maxs)
        fields["salary_currency"] = next(iter(currencies))
        if periods and len(set(periods)) == 1:
            fields["salary_period"] = periods[0]

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = job.get("title")
        location = job.get("location")
        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        url = job.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Percona",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "employment_type": job.get("employmentType"),
            "department": job.get("department"),
            "posted_at": job.get("publishedAt"),
            "apply_url": job.get("applyUrl"),
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        workplace = job.get("workplaceType")
        if not workplace and job.get("isRemote") is True:
            workplace = "Remote"
        if workplace:
            item["workplace_type"] = workplace

        item.update(_compensation_fields(job))

        # Drop fields that are absent rather than emitting empty/null values.
        yield {k: v for k, v in item.items() if v is not None and v != ""}
