import html
import json
import re

SITE = 'https://careers.kula.ai/api/internal/ats_job_posts?accountName=varo-money&type=ats_job_post.index&items=99&page=1'
EXTRA_HOSTS = []


def _html_to_text(value):
    value = value or ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("data") or []:
        if posting.get("listed") is not True:
            continue

        posting_id = posting.get("id")
        title = posting.get("title")
        ats_job = posting.get("ats_job") or {}
        description = _html_to_text(ats_job.get("job_description"))
        offices = ats_job.get("offices") or []
        locations = [
            office["location"]
            for office in offices
            if isinstance(office, dict) and office.get("location")
        ]

        if not posting_id or not title or not description or not locations:
            continue

        data = {
            "title": title.strip(),
            "company": "Varo",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": "Kula",
            "external_id": str(posting_id),
        }

        workplace = ats_job.get("workplace")
        if workplace:
            data["workplace_type"] = workplace

        employment_type = ats_job.get("employment_type")
        if employment_type:
            data["employment_type"] = employment_type

        department = (ats_job.get("ats_department") or {}).get("name")
        if department:
            data["department"] = department

        base_salary = (ats_job.get("compensation") or {}).get("base_salary") or {}
        minimum = base_salary.get("min_amount")
        maximum = base_salary.get("max_amount")
        currency = base_salary.get("currency")
        period = base_salary.get("interval")
        if minimum is not None:
            data["salary_min"] = float(minimum)
        if maximum is not None:
            data["salary_max"] = float(maximum)
        if currency:
            data["salary_currency"] = currency
        if period:
            data["salary_period"] = period

        url = "https://careers.kula.ai/varo-money/" + str(posting_id)
        data["url"] = url
        yield data
