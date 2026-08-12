import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://www.levels.fyi/jobs"
EXTRA_HOSTS = []


def _page_props(document):
    if not isinstance(document, str):
        return None

    marker = 'id="__NEXT_DATA__"'
    start = document.find(marker)
    if start < 0:
        marker = "id='__NEXT_DATA__'"
        start = document.find(marker)
    if start < 0:
        return None

    start = document.find(">", start)
    end = document.find("</script>", start)
    if start < 0 or end < 0:
        return None

    data = json.loads(document[start + 1:end])
    return data.get("props", {}).get("pageProps", {})


def _plain_text(value):
    if not isinstance(value, str) or not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?is)<(?:br|/p|/div|/li|/h[1-6])\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _first_value(record, names):
    for name in names:
        value = record.get(name)
        if value is not None and value != "":
            return value
    return None


def crawl(ctx):
    root = _page_props(ctx.fetch(SITE))
    if not root:
        return

    seen = set()
    groups = root.get("initialJobsData", {}).get("results", [])

    for group in groups:
        for listing in group.get("jobs", []):
            job_id = listing.get("id")
            if job_id is None:
                continue

            job_id = str(job_id)
            if job_id in seen:
                continue
            seen.add(job_id)

            url = SITE + "?jobId=" + job_id
            props = _page_props(ctx.fetch(url))
            if not props:
                continue

            details = props.get("initialJobDetails")
            if not isinstance(details, dict):
                continue

            title = _first_value(details, ("title", "jobTitle"))
            raw_description = _first_value(
                details,
                ("description", "descriptionHtml", "jobDescription"),
            )
            description = _plain_text(raw_description)

            company_info = details.get("companyInfo")
            company = (
                company_info.get("name")
                if isinstance(company_info, dict)
                else None
            )
            if not company:
                company = group.get("companyName")

            locations = details.get("locations")
            if not isinstance(locations, list):
                locations = listing.get("locations")
            if not isinstance(locations, list):
                locations = []

            locations = [
                value
                for value in locations
                if isinstance(value, str) and value.strip()
            ]
            location = (
                locations[0]
                if locations
                else _first_value(details, ("location", "locationName"))
            )

            if not title or not company or not location or not description:
                continue

            fields = {
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": "Levels.fyi",
                "external_id": job_id,
            }

            if locations:
                fields["locations"] = locations

            apply_url = _first_value(
                details,
                ("applicationUrl", "applyUrl"),
            )
            if apply_url:
                fields["apply_url"] = apply_url

            posted_at = _first_value(
                details,
                ("postingDate", "postedAt", "datePosted"),
            )
            if posted_at:
                fields["posted_at"] = posted_at

            expires_at = _first_value(
                details,
                ("expiryDate", "expiresAt", "validThrough"),
            )
            if expires_at:
                fields["expires_at"] = expires_at

            employment = _first_value(
                details,
                ("employmentTypes", "employmentType"),
            )
            if isinstance(employment, list):
                employment = ", ".join(
                    value
                    for value in employment
                    if isinstance(value, str) and value
                )
            if employment:
                fields["employment_type"] = employment

            workplace = _first_value(
                details,
                ("workArrangement", "workplaceType"),
            )
            if workplace:
                fields["workplace_type"] = workplace

            department = _first_value(details, ("department", "team"))
            if isinstance(department, dict):
                department = department.get("name")
            if department:
                fields["department"] = department

            salary_min = _first_value(
                details,
                ("minBaseSalary", "baseSalaryMin"),
            )
            salary_max = _first_value(
                details,
                ("maxBaseSalary", "baseSalaryMax"),
            )
            salary_currency = _first_value(
                details,
                ("baseSalaryCurrency", "salaryCurrency"),
            )
            salary_period = _first_value(
                details,
                ("baseSalaryPeriod", "salaryPeriod"),
            )

            if salary_min is not None:
                fields["salary_min"] = salary_min
            if salary_max is not None:
                fields["salary_max"] = salary_max
            if salary_currency:
                fields["salary_currency"] = salary_currency
            if salary_period:
                fields["salary_period"] = salary_period

            salary_text = _first_value(
                details,
                ("baseSalary", "salary", "salaryText"),
            )
            if isinstance(salary_text, str) and salary_text:
                fields["salary"] = salary_text

            yield Job(**fields)