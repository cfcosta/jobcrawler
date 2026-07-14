import json
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://datarobot.wd1.myworkdayjobs.com/wday/cxs/datarobot/DataRobot_External_Careers/jobs"
EXTRA_HOSTS = []

SITEMAP_URL = "https://datarobot.wd1.myworkdayjobs.com/DataRobot_External_Careers/siteMap.xml"
DETAIL_BASE = "https://datarobot.wd1.myworkdayjobs.com/wday/cxs/datarobot/DataRobot_External_Careers"
PUBLIC_PREFIX = "https://datarobot.wd1.myworkdayjobs.com/DataRobot_External_Careers"
COMPANY = "DataRobot, Inc."


def clean_description(value):
    if not value:
        return None

    text = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?is)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?is)</?\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|blockquote|pre)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    urls = re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", sitemap)
    seen = set()

    for encoded_url in urls[:12]:
        public_url = unescape(encoded_url.strip())
        if not public_url.startswith(PUBLIC_PREFIX + "/job/"):
            continue

        path = public_url[len(PUBLIC_PREFIX):]
        if not path or path in seen:
            continue
        seen.add(path)

        detail_url = urljoin(DETAIL_BASE + "/", path.lstrip("/"))
        detail = json.loads(ctx.fetch(detail_url))
        info = detail.get("jobPostingInfo") or {}

        if info.get("posted") is False or info.get("canApply") is False:
            continue

        title = info.get("title")
        location = info.get("location")
        description = clean_description(info.get("jobDescription"))
        canonical_url = info.get("externalUrl") or public_url

        if not title or not location or not description:
            continue

        fields = {
            "url": canonical_url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        additional_locations = info.get("additionalLocations") or []
        if additional_locations:
            fields["locations"] = [location] + [
                item
                for item in additional_locations
                if item and item != location
            ]

        if info.get("timeType"):
            fields["employment_type"] = info["timeType"]
        if info.get("startDate"):
            fields["posted_at"] = info["startDate"]
        if info.get("jobReqId"):
            fields["external_id"] = info["jobReqId"]
        if info.get("externalUrl"):
            fields["apply_url"] = info["externalUrl"]

        yield fields
