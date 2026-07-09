import json

SITE = (
    "https://api.ashbyhq.com/posting-api/job-board/goteleport?includeCompensation=true"
)
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _compensation(job):
    comp = job.get("compensation") or {}
    return _clean(
        comp.get("scrapeableCompensationSalarySummary")
        or comp.get("compensationTierSummary")
    )


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl")) or (
            SITE.rsplit("?", 1)[0].rstrip("/") + "/" + str(job.get("id"))
        )
        location = _clean(job.get("location"))

        item = {
            "url": url,
            "title": title,
            "company": "Teleport",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "department": _clean(job.get("department")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _compensation(job),
        }

        item = {k: v for k, v in item.items() if v is not None and v != ""}
        if item.get("title") and item.get("description") and item.get("url"):
            yield item
