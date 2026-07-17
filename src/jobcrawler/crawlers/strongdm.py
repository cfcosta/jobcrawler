
import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = "https://api.ashbyhq.com/posting-api/job-board/strongdm?includeCompensation=true"
EXTRA_HOSTS = []


def _plain(posting):
    text = posting.get("descriptionPlain")
    if text:
        return unescape(text).strip()

    html = posting.get("descriptionHtml") or ""
    html = re.sub(
        r"(?i)</?(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|br|hr)[^>]*>",
        "\n",
        html,
    )
    html = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", "", html)
    text = unescape(re.sub(r"(?s)<[^>]+>", "", html))
    return "\n".join(
        " ".join(line.split()) for line in text.splitlines() if line.strip()
    )


def _locations(posting):
    values = []
    primary = posting.get("location")
    if primary:
        values.append(primary)

    for secondary in posting.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value and value not in values:
            values.append(value)
    return values


def _compensation(posting):
    compensation = posting.get("compensation") or {}
    for component in compensation.get("summaryComponents") or []:
        low = component.get("minValue")
        high = component.get("maxValue")
        if low is not None or high is not None:
            return (
                low,
                high,
                component.get("currencyCode") or component.get("currency"),
                component.get("interval"),
            )
    return None, None, None, None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs") or []:
        locations = _locations(posting)
        salary_min, salary_max, salary_currency, salary_period = _compensation(
            posting
        )
        compensation = posting.get("compensation") or {}
        salary = (
            posting.get("compensationTierSummary")
            or compensation.get("compensationTierSummary")
        )

        workplace_type = posting.get("workplaceType")
        if not workplace_type and posting.get("isRemote") is True:
            workplace_type = "Remote"

        yield Job(
            url=posting.get("jobUrl"),
            title=posting.get("title"),
            company="strongDM",
            location=posting.get("location"),
            locations=locations or None,
            description=_plain(posting),
            salary=salary,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            employment_type=posting.get("employmentType"),
            workplace_type=workplace_type,
            department=posting.get("department") or posting.get("team"),
            posted_at=posting.get("publishedAt"),
            external_id=posting.get("id"),
            apply_url=posting.get("applyUrl"),
            source=SITE,
        )
