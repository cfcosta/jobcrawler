
import html
import json
import re

SITE = "https://celcoin.inhire.app/vagas"
EXTRA_HOSTS = ["api.inhire.app"]

_API = "https://api.inhire.app/job-posts/public/pages"
_HEADERS = {"X-Tenant": "celcoin"}


def _json(response):
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, str):
        return json.loads(response)
    try:
        return response.json()
    except (AttributeError, TypeError):
        return json.loads(response.text)


def _text(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|tr)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip() or None


def _payload(data):
    while isinstance(data, dict):
        for key in ("data", "result"):
            if isinstance(data.get(key), dict):
                data = data[key]
                break
        else:
            return data
    return data


def crawl(ctx):
    board = _payload(_json(ctx.fetch(_API, headers=_HEADERS)))
    jobs = board.get("jobsPage") or []

    for summary in jobs:
        status = str(summary.get("status") or "").lower()
        if status and status != "published":
            continue

        job_id = summary.get("jobId") or summary.get("id")
        if not job_id:
            continue

        detail = _payload(_json(ctx.fetch(
            "%s/%s" % (_API, job_id),
            headers=_HEADERS,
        )))
        if isinstance(detail.get("jobPage"), dict):
            detail = detail["jobPage"]
        elif isinstance(detail.get("job"), dict):
            detail = detail["job"]

        detail_status = str(detail.get("status") or status).lower()
        if detail_status and detail_status != "published":
            continue

        title = detail.get("displayName") or detail.get("title") or summary.get("displayName")
        description = _text(
            detail.get("description")
            or detail.get("jobDescription")
            or detail.get("descriptionHtml")
        )
        location = detail.get("location") or summary.get("location") or "Brasil"
        workplace = detail.get("workplaceType") or summary.get("workplaceType")
        url = "%s/%s" % (SITE.rstrip("/"), job_id)

        fields = {
            "url": url,
            "title": title,
            "company": "Celcoin",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
        }

        if workplace:
            fields["workplace_type"] = workplace

        employment_type = (
            detail.get("employmentType")
            or detail.get("contractType")
            or detail.get("jobType")
        )
        if isinstance(employment_type, list):
            employment_type = ", ".join(
                str(value).strip() for value in employment_type if str(value).strip()
            )
        if employment_type:
            fields["employment_type"] = employment_type

        department = (
            detail.get("department")
            or detail.get("area")
            or detail.get("team")
        )
        if isinstance(department, dict):
            department = department.get("name") or department.get("displayName")
        if department:
            fields["department"] = department

        apply_url = detail.get("applyUrl") or detail.get("applicationUrl")
        if apply_url:
            fields["apply_url"] = apply_url

        posted_at = detail.get("publishedAt") or detail.get("postedAt")
        if posted_at:
            fields["posted_at"] = posted_at

        expires_at = detail.get("expiresAt") or detail.get("expirationDate")
        if expires_at:
            fields["expires_at"] = expires_at

        yield fields
