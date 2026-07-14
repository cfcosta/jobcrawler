
import html
import json
import re

SITE = "https://intel471.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</\s*(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _json(response):
    if not isinstance(response, str):
        if hasattr(response, "text"):
            response = response.text
        elif hasattr(response, "json"):
            return response.json()
    response = response.strip()
    if response.startswith("<"):
        match = re.search(r"<pre[^>]*>(.*?)</pre>", response, re.I | re.S)
        if match:
            response = html.unescape(match.group(1))
    return json.loads(response)


def _location(job):
    values = []
    for source in (job.get("location") or {}, job.get("atsLocation") or {}):
        for key in ("city", "state", "province", "country", "addressCountry"):
            value = source.get(key)
            if value and value not in values:
                values.append(value)
    if values:
        return ", ".join(values)

    title = job.get("jobOpeningName") or ""
    match = re.search(r"\s+\(([^()]*)\)\s*$", title)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return "Not specified"


def crawl(ctx):
    listing = _json(ctx.fetch(SITE))
    rows = listing.get("result") or []

    for row in rows:
        job_id = str(row["id"])
        detail_url = f"https://intel471.bamboohr.com/careers/{job_id}/detail"
        detail = _json(ctx.fetch(detail_url))
        job = (detail.get("result") or {}).get("jobOpening") or {}

        if job.get("jobOpeningStatus") != "Open":
            continue

        url = job.get("jobOpeningShareUrl")
        title = job.get("jobOpeningName") or row.get("jobOpeningName")
        description = _text(job.get("description"))
        if not url or not title or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Intel 471",
            "location": _location(job),
            "description": description,
            "source": "BambooHR",
            "external_id": job_id,
            "apply_url": url,
        }

        optional = {
            "employment_type": job.get("employmentStatusLabel"),
            "department": job.get("departmentLabel"),
            "posted_at": job.get("datePosted"),
            "salary": job.get("compensation"),
        }
        for key, value in optional.items():
            if value:
                fields[key] = value

        suffix = re.search(r"\s+\(([^()]*)\)\s*$", title)
        if suffix and re.search(r"\bremote\b", suffix.group(1), re.I):
            fields["workplace_type"] = "Remote"

        yield fields
