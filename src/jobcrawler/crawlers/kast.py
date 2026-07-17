
import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = "https://careers.kast.xyz/postings.json"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return ""
    value = unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:div|p|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _description(posting):
    sections = []
    for body_key, heading_key in (
        ("description", None),
        ("key_responsibilities", "key_responsibilities_header"),
        ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
        ("benefits", "benefits_header"),
    ):
        body = _text(posting.get(body_key))
        if not body:
            continue
        heading = _text(posting.get(heading_key)) if heading_key else ""
        sections.append((heading + "\n" + body).strip())
    return "\n\n".join(sections)


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)
    for posting in payload.get("data", []):
        location = posting.get("location") or {}
        job_data = posting.get("job") or {}
        department = job_data.get("department") or {}
        kwargs = {
            "url": posting.get("url"),
            "title": posting.get("title"),
            "company": "KAST",
            "source": SITE,
            "location": location.get("name"),
            "description": _description(posting),
            "employment_type": posting.get("employment_type_text"),
            "workplace_type": posting.get("workplace_type_text"),
            "department": department.get("name"),
            "expires_at": posting.get("deadline_at"),
            "external_id": str(posting["id"]) if posting.get("id") is not None else None,
        }
        if posting.get("compensation_visible"):
            kwargs.update(
                salary=posting.get("compensation"),
                salary_min=posting.get("compensation_minimum"),
                salary_max=posting.get("compensation_maximum"),
                salary_currency=posting.get("compensation_currency"),
                salary_period=posting.get("compensation_frequency"),
            )
        yield Job(**{key: value for key, value in kwargs.items() if value not in (None, "")})
