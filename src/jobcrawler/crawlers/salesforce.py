
import html
import json
import re

SITE = "https://a.sfdcstatic.com/digital/xsf/careers/prod/jobs_2.json"
EXTRA_HOSTS = []


def _plain_text(value):
    text = re.sub(
        r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1\s*>",
        "",
        value,
    )
    text = re.sub(
        r"(?i)<\s*(br|hr)\b[^>]*?/?>|</?\s*(p|div|li|ul|ol|h[1-6]|"
        r"section|article|table|thead|tbody|tfoot|tr|td|th|blockquote)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(html.unescape(text)).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE)
    raw = response.text if hasattr(response, "text") else response
    payload = json.loads(raw)

    for item in payload.get("Report_Entry", []):
        url = item.get("External_Job_Posting_Site")
        title = item.get("Job_Posting_Title")
        location = item.get("Job_Requisition_Primary_Location")
        description = _plain_text(item.get("Job_Description") or "")
        if not (url and title and location and description):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Salesforce",
            "location": location,
            "description": description,
            "source": SITE,
            "locations": item.get("Locations") or None,
            "department": item.get("Job_Family_Group") or None,
            "employment_type": item.get("Time_Type") or None,
            "posted_at": item.get("External_Job_Posting_Start_Date") or None,
            "expires_at": item.get("External_Job_Posting_End_Date") or None,
            "external_id": item.get("Job_Requisition_Ref_ID") or None,
            "apply_url": url,
        }
        yield {key: value for key, value in fields.items() if value is not None}
