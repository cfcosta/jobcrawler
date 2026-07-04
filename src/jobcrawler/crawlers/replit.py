import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/replit?includeCompensation=true"

COMPANY = "Replit"

API_URL = (
    "https://api.ashbyhq.com/posting-api/job-board/replit?includeCompensation=true"
)


def crawl(ctx):
    raw = ctx.fetch(API_URL)
    data = json.loads(raw)
    jobs = data.get("jobs", [])
    for j in jobs:
        if not j.get("isListed", True):
            continue
        comp = j.get("compensation") or {}
        salary = (
            comp.get("scrapeableCompensationSalarySummary")
            or comp.get("compensationTierSummary")
            or ""
        )
        yield {
            "source": SITE,
            "url": j.get("jobUrl", ""),
            "title": j.get("title", ""),
            "company": COMPANY,
            "location": j.get("location", ""),
            "description": j.get("descriptionPlain", ""),
            "employment_type": j.get("employmentType", ""),
            "workplace_type": j.get("workplaceType", ""),
            "department": j.get("department", ""),
            "posted_at": j.get("publishedAt", ""),
            "external_id": j.get("id", ""),
            "apply_url": j.get("applyUrl", ""),
            "salary": salary,
        }
