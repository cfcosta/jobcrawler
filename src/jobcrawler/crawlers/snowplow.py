
import html
import json
import re

SITE = "https://snowplow.careers.hibob.com/api/job-ad"
EXTRA_HOSTS = []


def _plain(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    result = "\n".join(line for line in lines if line)
    return result or None


def _put(target, key, value):
    if value is not None and value != "":
        target[key] = value


def crawl(ctx):
    response = ctx.fetch(
        SITE,
        headers={
            "Accept": "application/json",
            "Referer": "https://snowplow.careers.hibob.com/",
        },
    )
    data = json.loads(response)
    for item in data.get("jobAdDetails", []):
        job_id = item.get("id")
        title = item.get("title")
        if not job_id or not title:
            continue

        sections = []
        for key in ("description", "responsibilities", "requirements", "benefits"):
            text = _plain(item.get(key))
            if text:
                sections.append(text)

        location = item.get("site") or item.get("country")
        url = f"https://snowplow.careers.hibob.com/jobs/{job_id}"
        fields = {
            "url": url,
            "title": title,
            "company": "Snowplow",
            "location": location,
            "description": "\n\n".join(sections),
            "source": SITE,
            "external_id": str(job_id),
            "apply_url": url,
        }
        _put(fields, "department", item.get("department"))
        _put(fields, "employment_type", item.get("employmentType"))
        _put(fields, "workplace_type", item.get("workspaceType"))
        _put(fields, "posted_at", item.get("publishedAt"))
        _put(fields, "salary_min", item.get("payTransparencyMinSalary"))
        _put(fields, "salary_max", item.get("payTransparencyMaxSalary"))
        _put(fields, "salary_currency", item.get("payTransparencySalaryCurrency"))
        _put(fields, "salary_period", item.get("payTransparencySalaryPayPeriod"))
        yield fields
