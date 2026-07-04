import json
import re
from datetime import datetime, timezone

SITE = "https://api.ashbyhq.com/posting-api/job-board/ramp?includeCompensation=true"


def crawl(ctx):
    """Yield Job dicts for every open Ramp role via the Ashby board API."""
    resp = ctx.fetch(SITE)
    data = json.loads(resp)
    jobs = data.get("jobs", [])

    for j in jobs:
        # Skip unlisted jobs
        if not j.get("isListed", True):
            continue

        job_url = j.get("jobUrl", "")
        title = j.get("title", "")
        description = j.get("descriptionPlain", "") or ""

        # Compensation
        salary = None
        comp = j.get("compensation")
        if comp:
            salary = comp.get("scrapeableCompensationSalarySummary") or None

        # Employment type
        employment_type = j.get("employmentType") or None

        # Workplace type: prefer workplaceType over isRemote
        workplace_type = j.get("workplaceType") or None
        if not workplace_type and j.get("isRemote"):
            workplace_type = "Remote"

        # Location
        location = j.get("location", "") or ""

        # Department
        department = j.get("department") or j.get("team") or None

        # Posted date
        posted_at = None
        pub = j.get("publishedAt")
        if pub:
            try:
                # Format: 2026-06-11T17:21:26.410+00:00
                posted_at = pub
            except Exception:
                posted_at = None

        # External ID
        external_id = j.get("id") or None

        # Apply URL
        apply_url = j.get("applyUrl") or None

        yield {
            "url": job_url,
            "title": title,
            "company": "Ramp",
            "location": location,
            "description": description,
            "salary": salary,
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "department": department,
            "posted_at": posted_at,
            "expires_at": None,
            "external_id": external_id,
            "apply_url": apply_url,
            "source": SITE,
        }
