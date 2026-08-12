
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _clean_html(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<\s*li(?:\s[^>]*)?>\s*(?:<\s*p(?:\s[^>]*)?>\s*)?",
        "\n- ",
        text,
    )
    text = re.sub(
        r"(?is)(?:<\s*/p\s*>\s*)?<\s*/li\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _location_names(job, detail):
    values = []
    for location in detail.get("locations") or job.get("locations") or []:
        option = location.get("location_option") or {}
        name = location.get("name") or option.get("display_name")
        if name:
            values.append(name)
    return values


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        department = group.get("name")
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True or listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _clean_html(detail.get("user_provided_description"))
            locations = _location_names(listed, detail)
            primary_location = detail.get("location") or (
                locations[0] if locations else None
            )
            compensation = detail.get("compensation") or {}

            fields = {
                "url": url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": primary_location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": url,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or listed.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            if department and department.lower() != "ungrouped":
                fields["department"] = department
            if detail.get("created"):
                fields["posted_at"] = detail["created"]
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]
            if compensation.get("lower_bound") is not None:
                fields["salary_min"] = compensation["lower_bound"]
            if compensation.get("upper_bound") is not None:
                fields["salary_max"] = compensation["upper_bound"]
            if compensation.get("currency_code"):
                fields["salary_currency"] = compensation["currency_code"]
            if compensation.get("salary_range_type"):
                fields["salary_period"] = compensation["salary_range_type"]

            yield Job(**fields)
