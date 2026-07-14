import html
import json
import re
from urllib.parse import urljoin, urlparse

SITE = 'https://www.careers-page.com/api/v1.0/c/cube-dev/jobs/?page_size=50&page=1'
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<br\s*/?>|</?(?:p|div|li|ul|ol|h[1-6])(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    next_url = SITE
    seen_pages = set()
    seen_jobs = set()
    site_host = urlparse(SITE).hostname

    while next_url:
        absolute_url = urljoin(SITE, next_url)
        if urlparse(absolute_url).hostname != site_host:
            raise ValueError("API pagination left the configured site host")
        if absolute_url in seen_pages:
            raise ValueError("API pagination loop detected")
        seen_pages.add(absolute_url)

        payload = json.loads(ctx.fetch(absolute_url))
        for item in payload.get("results") or []:
            job_id = str(item.get("id") or "").strip()
            job_hash = str(item.get("hash") or "").strip()
            title = str(item.get("position_name") or "").strip()
            location = str(item.get("location_display") or "").strip()
            description = _plain_text(item.get("description"))

            if not job_hash or job_hash in seen_jobs:
                continue
            seen_jobs.add(job_hash)

            # The API exposes one unfinished record without a body or location.
            if not title or not location or not description:
                continue

            job = {
                "url": "https://www.careers-page.com/cube-dev/job/" + job_hash,
                "title": title,
                "company": "Cube",
                "location": location,
                "description": description,
                "source": SITE,
            }
            if job_id:
                job["external_id"] = job_id
            yield job

        next_url = payload.get("next")
