import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

SITE = "https://ats.rippling.com/coin-metrics/jobs"
EXTRA_HOSTS = []


def _plain_text(*fragments):
    texts = []
    for fragment in fragments:
        if not fragment:
            continue
        text = str(fragment)
        text = re.sub(
            r"<(script|style|svg|noscript)\b[^>]*>.*?</\1\s*>",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(
            r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts)

def _next_data(document):
    match = re.search(
        r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script\s*>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return json.loads(html.unescape(match.group(1)))


def _listing_payload(document):
    data = _next_data(document)
    if not isinstance(data, dict):
        return None

    page_props = data.get("props", {}).get("pageProps", {})
    queries = page_props.get("dehydratedState", {}).get("queries", [])
    candidates = []
    for query in queries:
        state_data = query.get("state", {}).get("data")
        if not isinstance(state_data, dict) or not isinstance(state_data.get("items"), list):
            continue
        query_key = query.get("queryKey", [])
        if "job-posts" in query_key:
            return state_data
        candidates.append(state_data)
    return candidates[0] if candidates else None


def _page_url(page, page_size):
    parts = urlsplit(SITE)
    query = urlencode({"page": page, "pageSize": page_size})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _detail_payload(document):
    data = _next_data(document)
    if not isinstance(data, dict):
        return None
    payload = data.get("props", {}).get("pageProps", {}).get("apiData")
    return payload if isinstance(payload, dict) else None


def _clean_strings(values):
    result = []
    seen = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name") or value.get("label")
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _workplace_type(listing_item):
    values = []
    for location in listing_item.get("locations") or []:
        if not isinstance(location, dict):
            continue
        value = location.get("workplaceType")
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    unique = list(dict.fromkeys(values))
    return unique[0] if len(unique) == 1 else None


def _compensation(payload, post):
    ranges = payload.get("payRangeDetails")
    if not isinstance(ranges, list):
        ranges = post.get("payRangeDetails")
    ranges = [item for item in (ranges or []) if isinstance(item, dict)]
    if not ranges:
        return {}

    currencies = {item.get("currency") for item in ranges if item.get("currency")}
    periods = {item.get("frequency") for item in ranges if item.get("frequency")}
    if len(currencies) != 1 or len(periods) != 1:
        return {}

    minimums = [item.get("rangeStart") for item in ranges
                if isinstance(item.get("rangeStart"), (int, float))]
    maximums = [item.get("rangeEnd") for item in ranges
                if isinstance(item.get("rangeEnd"), (int, float))]
    result = {
        "salary_currency": next(iter(currencies)),
        "salary_period": next(iter(periods)),
    }
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    return result


def crawl(ctx):
    page_size = 1000
    page = 0
    listings = []
    seen_ids = set()

    while True:
        document = ctx.fetch(_page_url(page, page_size))
        payload = _listing_payload(document)
        if payload is None:
            # A correctly configured empty Rippling board has no dehydrated query.
            if page == 0:
                return
            break

        items = payload.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            external_id = item.get("id")
            dedupe_key = external_id or item.get("url")
            if dedupe_key and dedupe_key not in seen_ids:
                seen_ids.add(dedupe_key)
                listings.append(item)

        total_pages = payload.get("totalPages")
        if isinstance(total_pages, int):
            if page + 1 >= total_pages:
                break
        elif len(items) < page_size:
            break
        page += 1

    for listing_item in listings:
        url = listing_item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()

        payload = _detail_payload(ctx.fetch(url))
        if not payload:
            continue
        post = payload.get("jobPost")
        if not isinstance(post, dict):
            continue

        title = post.get("name") or listing_item.get("name")
        title = title.strip() if isinstance(title, str) else None
        description_parts = post.get("description") or {}
        if isinstance(description_parts, dict):
            description = _plain_text(
                description_parts.get("company"),
                description_parts.get("role"),
            )
        else:
            description = _plain_text(description_parts)
        if not title or not description:
            continue

        locations = _clean_strings(
            post.get("workLocations")
            or payload.get("workLocations")
            or listing_item.get("locations")
        )
        if not locations:
            continue

        kwargs = {
            "source": SITE,
            "url": post.get("url") or url,
            "title": title,
            "company": "Coin Metrics",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "external_id": post.get("uuid") or listing_item.get("id"),
        }

        department = payload.get("department") or post.get("department") or listing_item.get("department")
        if isinstance(department, dict):
            department = department.get("name")
        if isinstance(department, str) and department.strip():
            kwargs["department"] = department.strip()

        employment = post.get("employmentType")
        if isinstance(employment, dict):
            employment = employment.get("id") or employment.get("label")
        if isinstance(employment, str) and employment.strip():
            kwargs["employment_type"] = employment.strip()

        workplace = _workplace_type(listing_item)
        if workplace:
            kwargs["workplace_type"] = workplace

        created_on = post.get("createdOn")
        if isinstance(created_on, str) and created_on.strip():
            kwargs["posted_at"] = created_on.strip()

        kwargs.update(_compensation(payload, post))
        kwargs = {key: value for key, value in kwargs.items() if value is not None}
        yield kwargs
