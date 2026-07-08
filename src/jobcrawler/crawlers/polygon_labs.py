
import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/polygon-labs?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _pick_compensation(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("minValue") is None and component.get("maxValue") is None:
            continue
        salary = {}
        if component.get("minValue") is not None:
            salary["min"] = component.get("minValue")
        if component.get("maxValue") is not None:
            salary["max"] = component.get("maxValue")
        if component.get("currencyCode"):
            salary["currency"] = component.get("currencyCode")
        if component.get("interval"):
            salary["interval"] = component.get("interval")
        return salary or None
    return None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        if not description:
            continue

        item = {
            "url": job.get("jobUrl") or (SITE.split("?")[0].rstrip("/") + "/" + job.get("id", "")),
            "title": job.get("title"),
            "company": "Polygon Labs",
            "location": job.get("location"),
            "description": description,
            "source": SITE,
        }

        optional_map = {
            "id": "external_id",
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
        }
        for src, dst in optional_map.items():
            value = job.get(src)
            if value is not None and value != "":
                item[dst] = value

        salary = _pick_compensation(job)
        if salary:
            item["salary"] = salary

        apply_url = job.get("applyUrl") or job.get("applicationUrl")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
