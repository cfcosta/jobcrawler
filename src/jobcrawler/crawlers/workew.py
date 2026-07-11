
import json
import re
import html
from urllib.parse import urlencode

SITE = "https://workew.com/jm-ajax/get_listings/?orderby=date&order=DESC&per_page=20&page=1"
EXTRA_HOSTS = []

API = "https://workew.com/wp-json/wp/v2/job-listings"
FIELDS = "id,date,date_gmt,status,link,title,content,meta,job_listing_region,job-categories,job-types"

_BREAK_RE = re.compile(r"(?i)<\s*br\s*/?\s*>|</\s*(p|div|li|h[1-6]|tr|section|article|blockquote)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s+|\n{3,}")

def _clean_text(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _rendered(value):
    if isinstance(value, dict):
        return value.get("rendered")
    return value

def _meta(post, key):
    val = (post.get("meta") or {}).get(key)
    if val is None or val == "":
        return None
    return val

def _fetch_terms(ctx, rest_base):
    out = {}
    page = 1
    while True:
        url = "https://workew.com/wp-json/wp/v2/" + rest_base + "?" + urlencode({
            "per_page": 100,
            "page": page,
            "_fields": "id,name",
        })
        try:
            rows = json.loads(ctx.fetch(url))
        except Exception:
            break
        if not rows:
            break
        for row in rows:
            if isinstance(row, dict) and row.get("id") is not None and row.get("name"):
                out[int(row["id"])] = html.unescape(str(row["name"])).strip()
        if len(rows) < 100:
            break
        page += 1
    return out

def _names(ids, mapping):
    names = []
    for value in ids or []:
        try:
            name = mapping.get(int(value))
        except Exception:
            name = None
        if name:
            names.append(name)
    return names

def _salary_number(value):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return None

def crawl(ctx):
    regions = _fetch_terms(ctx, "job_listing_region")
    categories = _fetch_terms(ctx, "job-categories")
    types = _fetch_terms(ctx, "job-types")

    page = 1
    while True:
        url = API + "?" + urlencode({
            "per_page": 100,
            "page": page,
            "orderby": "date",
            "order": "desc",
            "_fields": FIELDS,
        })
        try:
            rows = json.loads(ctx.fetch(url))
        except Exception:
            if page == 1:
                raise
            break

        if not rows:
            break

        for post in rows:
            if post.get("status") and post.get("status") != "publish":
                continue

            title = _clean_text(_rendered(post.get("title")))
            desc = _clean_text(_rendered(post.get("content")))
            url = post.get("link")
            if not (url and title and desc):
                continue

            region_names = _names(post.get("job_listing_region"), regions)
            type_names = _names(post.get("job-types"), types)
            category_names = _names(post.get("job-categories"), categories)

            location = _clean_text(_meta(post, "_job_location"))
            if not location and region_names:
                location = region_names[0]

            item = {
                "url": url,
                "title": title,
                "company": _clean_text(_meta(post, "_company_name")) or "Workew",
                "location": location or "Remote",
                "description": desc,
                "source": SITE,
                "external_id": str(post.get("id")),
            }

            if post.get("date_gmt"):
                item["posted_at"] = str(post["date_gmt"]) + "Z"
            elif post.get("date"):
                item["posted_at"] = str(post["date"])

            apply_url = _meta(post, "_application")
            if apply_url:
                item["apply_url"] = str(apply_url)

            if type_names:
                item["employment_type"] = type_names[0]
            if category_names:
                item["department"] = category_names[0]
            if region_names:
                item["locations"] = region_names

            salary_text = _meta(post, "_job_salary")
            if salary_text:
                item["salary"] = str(salary_text)
                salary_min = _salary_number(salary_text)
                if salary_min is not None:
                    item["salary_min"] = salary_min

            currency = _meta(post, "_job_salary_currency")
            if currency:
                item["salary_currency"] = str(currency)

            unit = _meta(post, "_job_salary_unit")
            if unit:
                item["salary_period"] = str(unit)

            yield item

        if len(rows) < 100:
            break
        page += 1
