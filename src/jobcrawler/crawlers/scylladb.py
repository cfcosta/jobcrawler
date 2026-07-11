
import json
import re
from html import unescape

SITE = "https://www.comeet.co/careers-api/2.0/company/E4.006/positions?token=4E6187E1D64224A0013984E60187E&details=true"
EXTRA_HOSTS = []

def _strip_html(value):
    text = value or ""
    text = text.replace("\ufeff", "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _description(job):
    sections = []
    for detail in job.get("details") or []:
        name = (detail.get("name") or "").strip()
        value = _strip_html(detail.get("value") or "")
        if not value:
            continue
        if name:
            sections.append(name + "\n" + value)
        else:
            sections.append(value)
    return "\n\n".join(sections).strip()

def crawl(ctx):
    jobs = json.loads(ctx.fetch(SITE))
    for item in jobs:
        title = (item.get("name") or "").strip()
        loc_obj = item.get("location") or {}
        location = (loc_obj.get("name") or "").strip()
        description = _description(item)
        url = item.get("url_active_page") or item.get("url_comeet_hosted_page") or item.get("url_recruit_hosted_page")
        if not (title and location and description and url):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "ScyllaDB",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if item.get("uid"):
            out["external_id"] = item["uid"]
        if item.get("department"):
            out["department"] = item["department"]
        if item.get("time_updated"):
            out["posted_at"] = item["time_updated"]
        if item.get("employment_type"):
            out["employment_type"] = item["employment_type"]
        if item.get("workplace_type"):
            out["workplace_type"] = item["workplace_type"]
        if item.get("url_comeet_hosted_page"):
            out["apply_url"] = item["url_comeet_hosted_page"]

        yield out
