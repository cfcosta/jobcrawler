
import html
import json
import re


SITE = "https://careers.jobscore.com/careers/imgix/feed"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:address|article|aside|"
        r"blockquote|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _workplace_type(remote):
    if not remote:
        return None
    normalized = remote.lower()
    if normalized.startswith("yes") or "remotely 100%" in normalized:
        return "remote"
    if normalized.startswith("no") or "onsite all of the time" in normalized:
        return "onsite"
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    company = data.get("company_name") or data.get("company") or "Imgix"

    for item in data.get("jobs", []):
        fields = {
            "url": item.get("detail_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": company,
            "location": item.get("location"),
            "description": _html_to_text(item.get("description")),
            "department": item.get("department"),
            "employment_type": item.get("job_type"),
            "workplace_type": _workplace_type(item.get("remote")),
            "posted_at": item.get("opened_date"),
            "external_id": item.get("id"),
            "apply_url": item.get("apply_url"),
            "salary": item.get("formatted_public_compensation"),
            "salary_currency": item.get("currency_code"),
            "salary_period": item.get("public_compensation_interval"),
        }

        salary_min = item.get("public_salary_minimum")
        salary_max = item.get("public_salary_maximum")
        if salary_min is not None:
            fields["salary_min"] = salary_min / 100
        if salary_max is not None:
            fields["salary_max"] = salary_max / 100

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
