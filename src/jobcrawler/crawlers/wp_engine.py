
import html
import json
import re
from urllib.parse import urljoin


SITE = "https://wpengine.wd1.myworkdayjobs.com/wday/cxs/wpengine/WP_Engine/jobs"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _json(response):
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, str):
        return json.loads(response)
    if hasattr(response, "json"):
        value = response.json()
        return value
    if hasattr(response, "text"):
        value = response.text
        if callable(value):
            value = value()
        return json.loads(value)
    return json.loads(response)


def crawl(ctx):
    offset = 0
    limit = 20
    seen = set()

    while True:
        payload = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        data = _json(ctx.fetch(SITE, method="POST", json=payload))
        postings = data.get("jobPostings") or []

        for posting in postings:
            path = posting.get("externalPath")
            if not path:
                continue

            detail_url = urljoin(
                "https://wpengine.wd1.myworkdayjobs.com/wday/cxs/wpengine/WP_Engine/",
                path.lstrip("/"),
            )
            detail = _json(ctx.fetch(detail_url))
            info = detail.get("jobPostingInfo") or {}

            if not info.get("posted", True) or not info.get("canApply", True):
                continue

            external_id = info.get("jobReqId")
            dedupe_key = external_id or path
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            description = _plain_text(info.get("jobDescription"))
            title = info.get("title") or posting.get("title")
            location = info.get("location") or posting.get("locationsText")
            canonical_url = info.get("externalUrl") or urljoin(
                "https://wpengine.wd1.myworkdayjobs.com/WP_Engine/",
                path.lstrip("/"),
            )
            if not title or not location or not description:
                continue

            kwargs = {
                "url": canonical_url,
                "source": SITE,
                "title": title,
                "company": "WP Engine",
                "location": location,
                "description": description,
                "external_id": external_id,
                "apply_url": info.get("externalUrl"),
                "employment_type": info.get("timeType"),
                "posted_at": info.get("startDate"),
            }

            additional = info.get("additionalLocations") or []
            locations = [location] + [
                item for item in additional if item and item != location
            ]
            if len(locations) > 1:
                kwargs["locations"] = locations

            yield {
                key: value for key, value in kwargs.items()
                if value is not None and value != ""
            }

        offset += len(postings)
        total = data.get("total")
        if not postings or (isinstance(total, int) and offset >= total):
            break
        if len(postings) < limit:
            break
