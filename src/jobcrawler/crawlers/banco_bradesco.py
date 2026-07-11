
import json
import re

SITE = "https://us.api.csod.com/rec-job-search/external/jobs"
EXTRA_HOSTS = ["bradesco.csod.com"]

BOOTSTRAP_URL = "https://bradesco.csod.com/ux/ats/careersite/1/home?c=bradesco"
COMPANY = "Bradesco"


def _parse_context(html):
    m = re.search(r"csod\.context\s*=\s*(\{.*?\})\s*;", html, re.S)
    if not m:
        raise RuntimeError("CSOD context not found")
    return json.loads(m.group(1))


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _location_name(loc):
    if not isinstance(loc, dict):
        return None
    parts = []
    for key in ("city", "state", "country"):
        value = _clean(loc.get(key))
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _date_br(value):
    value = _clean(value)
    if not value:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if not m:
        return None
    day, month, year = m.groups()
    return "%s-%02d-%02d" % (year, int(month), int(day))


def crawl(ctx):
    html = ctx.fetch(BOOTSTRAP_URL)
    context = _parse_context(html)
    token = context.get("token")
    if not token:
        raise RuntimeError("CSOD token not found")

    cloud = context.get("endpoints", {}).get("cloud") or "https://us.api.csod.com/"
    api_url = cloud.rstrip("/") + "/rec-job-search/external/jobs"

    payload = {
        "careerSiteId": 1,
        "careerSitePageId": 1,
        "pageNumber": 1,
        "pageSize": 500,
        "cultureId": 12,
        "searchText": "",
        "cultureName": "pt-BR",
        "states": [],
        "countryCodes": [],
        "cities": [],
        "placeID": None,
        "radius": 0,
        "postingsWithinDays": None,
        "customFieldCheckboxKeys": [],
        "customFieldDropdowns": [],
        "customFieldRadios": [],
    }
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    raw = ctx.fetch(api_url, method="POST", json=payload, headers=headers)
    data = json.loads(raw)
    requisitions = data.get("data", {}).get("requisitions", [])

    for req in requisitions:
        title = _clean(req.get("displayJobTitle") or req.get("title"))
        description = _clean(req.get("externalDescription") or req.get("description"))
        if not title or not description:
            continue

        locations = []
        for loc in req.get("locations") or []:
            name = _location_name(loc)
            if name and name not in locations:
                locations.append(name)

        location = locations[0] if locations else "Brasil"
        external_id = _clean(req.get("requisitionId") or req.get("id"))

        # The human SPA requisition route redirects to login; this API row has the full posting.
        url = SITE
        if external_id:
            url = SITE + "?requisitionId=" + external_id

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        if locations:
            job["locations"] = locations
        if external_id:
            job["external_id"] = external_id

        posted_at = _date_br(req.get("postingEffectiveDate"))
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = _date_br(req.get("postingExpirationDate"))
        if expires_at:
            job["expires_at"] = expires_at

        yield job
