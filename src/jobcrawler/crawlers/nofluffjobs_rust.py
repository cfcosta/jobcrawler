import json
import re
from html import unescape

SITE = "https://nofluffjobs.com/Rust"
EXTRA_HOSTS = []

SEARCH_BODY = {"criteriaSearch": {"jobPosition": ["rust"]}, "pageSize": 100}


def _clean_text(value):
    if not value:
        return None
    value = unescape(str(value)).replace("\xa0", " ")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(
        r"</(?:p|div|li|ul|ol|h[1-6]|section|article)>", "\n", value, flags=re.I
    )
    value = re.sub(r"<script[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<svg[^>]*>.*?</svg>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip() or None


def _page_text(html):
    body = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
    body = re.sub(r"<svg[^>]*>.*?</svg>", " ", body, flags=re.I | re.S)
    body = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", body, flags=re.I | re.S)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(
        r"</(?:p|div|li|ul|ol|h[1-6]|section|article)>", "\n", body, flags=re.I
    )
    body = re.sub(r"<[^>]+>", " ", body)
    body = unescape(body).replace("\xa0", " ")
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


def _jsonld_job(html):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.S | re.I,
    )
    for raw in scripts:
        try:
            data = json.loads(unescape(raw).strip())
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes = data.get("@graph")
        elif isinstance(data, list):
            nodes = data
        else:
            nodes = [data]
        for node in nodes:
            if isinstance(node, dict):
                typ = node.get("@type")
                if typ == "JobPosting" or (
                    isinstance(typ, list) and "JobPosting" in typ
                ):
                    return node
    return {}


def _full_description(html, ld):
    text = _page_text(html)
    starts = []
    for marker in ("Must have", "Requirements description", "Offer description"):
        pos = text.find(marker)
        if pos >= 0:
            starts.append(pos)
    stops = []
    for marker in (
        "See also similar ads",
        "Receive tailored job offers",
        "Apply\nSave job offer",
        "Rate this offer",
    ):
        pos = text.find(marker)
        if pos >= 0:
            stops.append(pos)

    parts = []
    if starts:
        start = min(starts)
        after = []
        for pos in stops:
            if pos > start:
                after.append(pos)
        stop = min(after) if after else len(text)
        segment = text[start:stop].strip()
        segment = re.sub(
            r"\n(?:Original text\.|Show translation|show all|\(\d+\))\n", "\n", segment
        )
        segment = re.sub(r"\n\s*\n+", "\n", segment).strip()
        if len(segment) > 300:
            parts.append(segment)

    ld_desc = _clean_text(ld.get("description"))
    if ld_desc and not any(ld_desc in p for p in parts):
        parts.append(ld_desc)

    if parts:
        return "\n\n".join(parts).strip()
    return ld_desc or ""


def _location(posting, ld):
    places = []
    location = posting.get("location") or {}
    for place in location.get("places") or []:
        city = place.get("city")
        country = (place.get("country") or {}).get("name")
        if city and city != "Remote":
            loc = city
            if country:
                loc = city + ", " + country
            if loc not in places:
                places.append(loc)
    if location.get("fullyRemote"):
        if places:
            return "Remote (" + "; ".join(places) + ")"
        return "Remote"
    if places:
        return "; ".join(places)

    locs = ld.get("jobLocation")
    if isinstance(locs, dict):
        locs = [locs]
    if isinstance(locs, list):
        vals = []
        for loc in locs:
            addr = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr, dict):
                pieces = []
                if addr.get("addressLocality"):
                    pieces.append(addr.get("addressLocality"))
                if addr.get("addressCountry"):
                    pieces.append(addr.get("addressCountry"))
                if pieces:
                    vals.append(", ".join(pieces))
        if vals:
            return "; ".join(vals)
    if ld.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    return "Not specified"


def _posted_at(ms_or_text, ld):
    if ld.get("datePosted"):
        return ld.get("datePosted")
    try:
        import datetime

        return (
            datetime.datetime.fromtimestamp(
                int(ms_or_text) / 1000, datetime.timezone.utc
            )
            .date()
            .isoformat()
        )
    except Exception:
        return None


def _salary(posting):
    salary = posting.get("salary") or {}
    if salary.get("disclosedAt") != "VISIBLE":
        return None
    currency = salary.get("currency") or ""
    sal_type = salary.get("type") or ""
    low = salary.get("from")
    high = salary.get("to")
    if low is None and high is None:
        return None
    if low is not None and high is not None:
        text = str(low) + " - " + str(high)
    elif low is not None:
        text = "from " + str(low)
    else:
        text = "up to " + str(high)
    if currency:
        text = text + " " + currency
    text = text + " per month"
    if sal_type:
        text = text + " (" + str(sal_type) + ")"
    return text


def crawl(ctx):
    seen = set()
    first_url = "https://nofluffjobs.com/api/search/posting?page=1&salaryCurrency=PLN&salaryPeriod=month"
    first = json.loads(ctx.fetch(first_url, method="POST", json=SEARCH_BODY))
    pages = int(first.get("exactMatchesPages") or 1)
    payloads = [first]

    for page in range(2, pages + 1):
        page_url = f"https://nofluffjobs.com/api/search/posting?page={page}&salaryCurrency=PLN&salaryPeriod=month"
        payloads.append(
            json.loads(ctx.fetch(page_url, method="POST", json=SEARCH_BODY))
        )

    for payload in payloads:
        for posting in payload.get("postings") or []:
            ref = posting.get("reference") or posting.get("id") or posting.get("url")
            if not ref or ref in seen:
                continue
            seen.add(ref)

            slug = posting.get("url")
            if not slug:
                continue
            url = "https://nofluffjobs.com/job/" + slug.lstrip("/")
            html = ctx.fetch(url)
            ld = _jsonld_job(html)
            org = (
                ld.get("hiringOrganization")
                if isinstance(ld.get("hiringOrganization"), dict)
                else {}
            )

            job = {
                "url": url,
                "title": (posting.get("title") or ld.get("title") or "").strip(),
                "company": (posting.get("name") or org.get("name") or "").strip(),
                "location": _location(posting, ld),
                "description": _full_description(html, ld),
                "source": SITE,
                "external_id": ref,
                "apply_url": url,
            }

            posted = _posted_at(posting.get("posted"), ld)
            if posted:
                job["posted_at"] = posted
            if ld.get("validThrough"):
                job["expires_at"] = ld.get("validThrough")
            if (posting.get("location") or {}).get("fullyRemote") or ld.get(
                "jobLocationType"
            ) == "TELECOMMUTE":
                job["workplace_type"] = "remote"
            employment = (posting.get("salary") or {}).get("type") or ld.get(
                "employmentType"
            )
            if employment:
                job["employment_type"] = str(employment).lower()
            department = posting.get("category") or ld.get("occupationalCategory")
            if department:
                job["department"] = department
            sal = _salary(posting)
            if sal:
                job["salary"] = sal

            yield {k: v for k, v in job.items() if v not in (None, "")}
