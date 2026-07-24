
import json
import re
import html

SITE = "https://career.globant.com/api/sap/job-requisition-v1"
EXTRA_HOSTS = []

def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|tr|h[1-6])(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()

def _slug(title):
    # Globant's public job URLs remove spaces and punctuation while keeping letters such as accents.
    slug = "".join(ch for ch in (title or "") if ch.isalnum())
    return slug or "job"

def _job_url(title, job_id):
    return f"https://career.globant.com/job/{_slug(title)}/{job_id}"

def _fetch_page(ctx, page):
    return json.loads(ctx.fetch(SITE, method="POST", json={"page": page}))

def crawl(ctx):
    page = 1
    total_pages = None
    seen = set()

    while True:
        data = _fetch_page(ctx, page)
        jobs = data.get("jobRequisition") or []

        if total_pages is None:
            total_pages = data.get("totalPages")

        for item in jobs:
            job_id = str(item.get("jobReqId") or "").strip()
            title = (item.get("jobTitle") or "").strip()
            description = _html_to_text(item.get("jobDescription") or "")
            location = (item.get("location") or item.get("country") or "").strip()

            if not job_id or not title or not description or not location or job_id in seen:
                continue
            seen.add(job_id)

            labels = []
            for area in item.get("area") or []:
                if isinstance(area, dict):
                    label = (area.get("label") or "").strip()
                    if label:
                        labels.append(label)

            job = {
                "url": _job_url(title, job_id),
                "title": title,
                "company": "Globant",
                "location": location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            posted_at = (item.get("createdDateTime") or "").strip()
            if posted_at:
                job["posted_at"] = posted_at

            if labels:
                job["department"] = ", ".join(labels)

            yield job

        if total_pages is not None:
            if page >= int(total_pages):
                break
        elif not data.get("showMore") or not jobs:
            break

        page += 1
