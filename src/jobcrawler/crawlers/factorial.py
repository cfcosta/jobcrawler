
import html
import json
import re

SITE = "https://careers.factorialhr.com/"
EXTRA_HOSTS = []


def _clean(value):
    if not isinstance(value, str):
        return None
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _cards(page):
    pattern = r"<li\b([^>]*\bclass=['\"][^'\"]*\bjob-offer-item\b[^'\"]*['\"][^>]*)>(.*?)</li>"
    for attrs, body in re.findall(pattern, page, flags=re.I | re.S):
        match = re.search(
            r"\bdata-job-postings-url=['\"]([^'\"]+)['\"]", attrs, flags=re.I
        )
        if not match:
            continue
        url = html.unescape(match.group(1)).strip()
        divs = []
        for content in re.findall(r"<div\b[^>]*>(.*?)</div>", body, flags=re.I | re.S):
            text = _clean(content)
            if text:
                divs.append(text)
        title = divs[0] if divs else None
        department = divs[1] if len(divs) > 1 else None
        workplace = divs[2] if len(divs) > 2 else None
        contract = re.search(r"\bdata-contract-type=['\"]([^'\"]+)['\"]", attrs, re.I)
        yield {
            "url": url,
            "title": title,
            "department": department,
            "workplace_type": workplace,
            "employment_type": _clean(contract.group(1)) if contract else None,
        }


def _posting(page):
    pattern = r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>"
    for raw in re.findall(pattern, page, flags=re.I | re.S):
        try:
            data = json.loads(raw)
        except ValueError:
            try:
                data = json.loads(html.unescape(raw))
            except ValueError:
                continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return None


def _location(data):
    place = data.get("jobLocation")
    if isinstance(place, list):
        place = place[0] if place else None
    if not isinstance(place, dict):
        return _clean(place)
    address = place.get("address")
    if isinstance(address, dict):
        for key in ("addressLocality", "streetAddress", "addressRegion", "addressCountry"):
            value = address.get(key)
            if isinstance(value, dict):
                value = value.get("name")
            value = _clean(value)
            if value:
                return value
    return _clean(place.get("name"))


def crawl(ctx):
    cards = list(_cards(ctx.fetch(SITE)))
    seen = set()

    for card in cards:
        if len(seen) >= 24:
            break
        url = card["url"]
        if not url.startswith(SITE + "job_posting/") or url in seen:
            continue
        seen.add(url)

        data = _posting(ctx.fetch(url))
        if not data:
            continue
        title = _clean(data.get("title")) or card["title"]
        description = _clean(data.get("description"))
        location = _location(data)
        if not title or not description or not location:
            continue

        identifier = data.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if identifier is None:
            match = re.search(r"-(\d+)(?:[/?#]|$)", url)
            identifier = match.group(1) if match else None

        job = {
            "url": url,
            "apply_url": url,
            "title": title,
            "company": "Factorial",
            "location": location,
            "description": description,
            "source": SITE,
        }
        for key in ("department", "workplace_type", "employment_type"):
            if card.get(key):
                job[key] = card[key]
        if identifier is not None and str(identifier).strip():
            job["external_id"] = str(identifier).strip()
        posted = _clean(data.get("datePosted"))
        if posted:
            job["posted_at"] = posted
        expires = _clean(data.get("validThrough"))
        if expires:
            job["expires_at"] = expires
        yield job
