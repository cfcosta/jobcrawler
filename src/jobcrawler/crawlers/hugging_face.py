
import json
import re

SITE = "https://apply.workable.com/huggingface/?lng=en"
EXTRA_HOSTS = []

API_URL = "https://apply.workable.com/api/v1/widget/accounts/huggingface"


def _clean_markdown(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _location(job):
    locs = []
    for loc in job.get("locations") or []:
        parts = []
        for key in ("city", "region", "country"):
            val = loc.get(key)
            if val and val not in parts:
                parts.append(val)
        if parts:
            locs.append(", ".join(parts))
    if locs:
        base = "; ".join(locs)
    else:
        parts = [job.get("city"), job.get("state"), job.get("country")]
        base = ", ".join([p for p in parts if p])
    if job.get("telecommuting"):
        return (base + " (Remote)") if base else "Remote"
    return base or None


def _field(obj, name):
    value = obj.get(name)
    if value == "":
        return None
    return value


def crawl(ctx):
    data = json.loads(ctx.fetch(API_URL))
    company = data.get("name") or "Hugging Face"

    for job in data.get("jobs") or []:
        shortcode = job.get("shortcode")
        if not shortcode:
            continue

        detail_md_url = "https://apply.workable.com/huggingface/jobs/view/%s.md" % shortcode
        description = _clean_markdown(ctx.fetch(detail_md_url))
        if not description:
            continue

        item = {
            "url": job.get("url") or detail_md_url,
            "title": job.get("title"),
            "company": company,
            "location": _location(job),
            "description": description,
            "source": SITE,
            "external_id": shortcode,
        }

        employment_type = _field(job, "employment_type")
        if employment_type:
            item["employment_type"] = employment_type

        department = _field(job, "department")
        if department:
            item["department"] = department

        posted_at = _field(job, "published_on") or _field(job, "created_at")
        if posted_at:
            item["posted_at"] = posted_at

        apply_url = _field(job, "application_url")
        if apply_url:
            item["apply_url"] = apply_url

        if job.get("telecommuting") is True:
            item["workplace_type"] = "remote"

        yield item
