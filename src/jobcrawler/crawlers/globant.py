
import html
import json
import re

SITE = 'https://career.globant.com/api/sap/job-requisition-v1'
EXTRA_HOSTS = []


def html_to_text(value):
    text = value or ""
    text = re.sub(r"<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)\s*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?(?:p|div|ul|ol|h1|h2|h3|h4|h5|h6|section|article|tr|table|blockquote)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def job_url(title, external_id):
    slug = "".join(character for character in title if character.isalnum())
    return "https://career.globant.com/job/" + slug + "/" + str(external_id)


def crawl(ctx):
    page = 1

    while True:
        data = json.loads(ctx.fetch(SITE, method="POST", json={"page": page}))
        items = data.get("jobRequisition") or []

        for item in items:
            title = item.get("jobTitle")
            external_id = item.get("jobReqId")
            description = html_to_text(item.get("jobDescription"))

            if not title or external_id is None or not description:
                continue

            job = {
                "url": job_url(title, external_id),
                "title": title,
                "company": "Globant",
                "location": item.get("location") or item.get("country"),
                "description": description,
                "source": SITE,
                "external_id": str(external_id),
            }

            posted_at = item.get("createdDateTime")
            if posted_at:
                job["posted_at"] = posted_at

            labels = []
            for area in item.get("area") or []:
                if isinstance(area, dict) and area.get("label"):
                    labels.append(area["label"])
            if labels:
                job["department"] = ", ".join(labels)

            yield job

        total_pages = data.get("totalPages")
        if isinstance(total_pages, int):
            if page >= total_pages:
                break
        elif not data.get("showMore") or not items:
            break

        page += 1
