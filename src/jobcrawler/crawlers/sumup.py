
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://www.sumup.com/careers/"
EXTRA_HOSTS = []

MAX_DETAIL_PAGES = 30


def _fetch_text(ctx, url):
    res = ctx.fetch(url)
    if hasattr(res, "text"):
        return res.text
    return res


def _next_data(page_html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page_html or "", re.S)
    if not m:
        return None
    return json.loads(html.unescape(m.group(1)))


def _page_from_next(data):
    try:
        return data["props"]["pageProps"]["page"]
    except Exception:
        return {}


def _clean_text(text):
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _rich_text_to_text(node):
    parts = []

    def rec(n):
        if n is None:
            return
        if isinstance(n, str):
            parts.append(n)
            return
        if isinstance(n, list):
            for item in n:
                rec(item)
            return
        if not isinstance(n, dict):
            return

        node_type = n.get("nodeType")
        if node_type == "text":
            parts.append(n.get("value") or "")
            return

        if node_type in {
            "paragraph", "heading-1", "heading-2", "heading-3",
            "heading-4", "heading-5", "heading-6", "unordered-list",
            "ordered-list", "list-item", "hr",
        }:
            parts.append("\n")
            rec(n.get("content"))
            parts.append("\n")
        else:
            rec(n.get("content"))

    rec(node)
    return _clean_text("".join(parts))


def _listing_jobs(html_text):
    data = _next_data(html_text)
    page = _page_from_next(data or {})
    jobs = page.get("greenhouse") or []
    return [j for j in jobs if isinstance(j, dict) and j.get("url") and j.get("title")]


def _compact(data):
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def crawl(ctx):
    listing_url = urljoin(SITE, "/careers/positions/")
    listing_html = _fetch_text(ctx, listing_url)

    seen = set()
    checked = 0
    for listing in _listing_jobs(listing_html):
        if checked >= MAX_DETAIL_PAGES:
            break

        detail_url = urljoin(SITE, listing.get("url"))
        if detail_url in seen:
            continue
        seen.add(detail_url)
        checked += 1

        try:
            detail_html = _fetch_text(ctx, detail_url)
            page = _page_from_next(_next_data(detail_html) or {})
        except Exception:
            continue
        if not page:
            continue

        title = page.get("title") or listing.get("title")
        company = page.get("company_name") or "SumUp"

        location_obj = page.get("location") or listing.get("location") or {}
        if isinstance(location_obj, dict):
            location = location_obj.get("city") or location_obj.get("name")
        else:
            location = location_obj

        locations = []
        for office in page.get("offices") or []:
            if isinstance(office, dict):
                loc = office.get("location") or office.get("name")
            else:
                loc = office
            if loc and loc not in locations:
                locations.append(loc)
        if not location and locations:
            location = locations[0]

        description = _rich_text_to_text(page.get("content"))
        if not (title and company and location and description):
            continue

        job = {
            "url": detail_url,
            "title": title,
            "company": company,
            "location": location,
            "locations": locations,
            "description": description,
            "department": page.get("department") or listing.get("department"),
            "posted_at": page.get("first_published"),
            "expires_at": page.get("application_deadline"),
            "external_id": str(page.get("internal_job_id") or listing.get("shortcode") or ""),
            "source": SITE,
        }

        if page.get("absolute_url"):
            job["apply_url"] = page.get("absolute_url")

        yield _compact(job)
