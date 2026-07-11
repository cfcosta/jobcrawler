
SITE = "https://redhat.wd5.myworkdayjobs.com/wday/cxs/redhat/jobs/jobs"
EXTRA_HOSTS = []

import html
import json
import re

SITEMAP_URL = "https://redhat.wd5.myworkdayjobs.com/Jobs/siteMap.xml"
MAX_JOBS = 10

def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _detail_url_from_human(url):
    marker = "/Jobs/job/"
    idx = url.find(marker)
    if idx < 0:
        return None
    return SITE.rsplit("/jobs", 1)[0] + "/job/" + url[idx + len(marker):]

def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    seen = set()
    yielded = 0
    for match in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", xml):
        if yielded >= MAX_JOBS:
            break
        human_url = html.unescape(match.group(1).strip())
        if "/Jobs/job/" not in human_url:
            continue
        detail_url = _detail_url_from_human(human_url)
        if not detail_url or detail_url in seen:
            continue
        seen.add(detail_url)
        detail = json.loads(ctx.fetch(detail_url))
        info = detail.get("jobPostingInfo") or {}
        description = _text_from_html(info.get("jobDescription"))
        title = info.get("title")
        location = info.get("location")
        if not (title and location and description):
            continue
        job = {
            "url": info.get("externalUrl") or human_url,
            "title": title,
            "company": "Red Hat",
            "location": location,
            "description": description,
            "external_id": info.get("jobReqId"),
            "source": SITE,
        }
        if info.get("timeType"):
            job["employment_type"] = info.get("timeType")
        if info.get("remoteType"):
            job["workplace_type"] = info.get("remoteType")
        if info.get("endDate"):
            job["expires_at"] = info.get("endDate")
        if info.get("externalUrl"):
            job["apply_url"] = info.get("externalUrl")
        yielded += 1
        yield job
