
import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/weights_and_biases/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value or "")
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</\s*(?:address|article|aside|blockquote|"
        r"dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|"
        r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text)

    lines = [
        re.sub(r"[ \t\f\v]+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ]
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    data = json.loads(response)

    jobs = data.get("jobs", [])
    total = data.get("meta", {}).get("total")
    if total is not None and total != len(jobs):
        raise ValueError("Greenhouse response did not contain the complete board")

    for item in jobs:
        url = item.get("absolute_url")
        title = item.get("title")
        company = item.get("company_name")
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))

        if not all((url, title, company, location, description)):
            continue

        job = {
            "url": url,
            "apply_url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": "Greenhouse",
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = ", ".join(departments)

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        yield job
