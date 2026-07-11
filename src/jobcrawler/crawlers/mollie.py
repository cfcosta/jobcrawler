import json
import re
from html import unescape

# Mollie's careers site (jobs.mollie.com/vacancies) is a JS-hydrated Framer
# front-end with no server-rendered JobPosting data. Every vacancy links out to
# Ashby (jobs.ashbyhq.com/Mollie/<uuid>) and the branded /vacancies/<uuid> slug
# is exactly that Ashby job id, so the real data source is the Ashby posting API.
SITE = "https://api.ashbyhq.com/posting-api/job-board/Mollie?includeCompensation=true"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def _strip_html(value):
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = job.get("title")
        description = job.get("descriptionPlain") or _strip_html(
            job.get("descriptionHtml")
        )
        job_id = job.get("id")
        # Prefer the branded Mollie vacancy URL (the board we crawl); the slug is
        # the Ashby job id. Fall back to Ashby's own jobUrl if the id is missing.
        url = (
            "https://jobs.mollie.com/vacancies/{}".format(job_id)
            if job_id
            else job.get("jobUrl")
        )
        location = job.get("location")
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Mollie",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if job_id:
            out["external_id"] = job_id

        apply_url = job.get("applyUrl") or job.get("jobUrl")
        if apply_url:
            out["apply_url"] = apply_url

        employment_type = job.get("employmentType")
        if employment_type:
            out["employment_type"] = employment_type

        workplace_type = job.get("workplaceType")
        if not workplace_type and job.get("isRemote"):
            workplace_type = "Remote"
        if workplace_type:
            out["workplace_type"] = workplace_type

        department = job.get("department") or job.get("team")
        if department:
            out["department"] = department

        posted_at = job.get("publishedAt")
        if posted_at:
            out["posted_at"] = posted_at

        locations = []
        if location:
            locations.append(location)
        for secondary in job.get("secondaryLocations") or []:
            if isinstance(secondary, dict):
                secondary_location = secondary.get("location") or secondary.get("name")
            else:
                secondary_location = secondary
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)
        if len(locations) > 1:
            out["locations"] = locations

        comp = job.get("compensation") or {}
        summary = comp.get("scrapeableCompensationSalarySummary") or comp.get(
            "compensationTierSummary"
        )
        if summary:
            out["salary"] = summary

        components = comp.get("summaryComponents") or []
        if len(components) == 1:
            component = components[0] or {}
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")
            if min_value is not None:
                out["salary_min"] = min_value
            if max_value is not None:
                out["salary_max"] = max_value
            if currency:
                out["salary_currency"] = currency
            if interval:
                out["salary_period"] = interval

        yield out
