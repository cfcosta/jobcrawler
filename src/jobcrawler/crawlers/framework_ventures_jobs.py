
import html
import json
import re

SITE = "https://jobs.framework.ventures/"
EXTRA_HOSTS = []
SITEMAP_URL = "https://jobs.framework.ventures/sitemap.xml"



def _text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(
        r"<\s*(?:br|p|div|li|ul|ol|h[1-6])\b[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"</\s*(?:p|div|li|ul|ol|h[1-6])\s*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _script(page, attributes):
    pattern = r"<script[^>]*" + attributes + r"[^>]*>(.*?)</script>"
    match = re.search(pattern, page, re.I | re.S)
    return match.group(1) if match else None


def _jobposting(value):
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            return value
        if "@graph" in value:
            return _jobposting(value["@graph"])
    if isinstance(value, list):
        for item in value:
            found = _jobposting(item)
            if found:
                return found
    return None


def _location_name(place):
    if not isinstance(place, dict):
        return place if isinstance(place, str) and place.strip() else None
    address = place.get("address")
    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, dict):
        return place.get("name")
    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        if value and value not in parts:
            parts.append(str(value))
    return ", ".join(parts) if parts else address.get("streetAddress")


def _salary(ld, state):
    base = ld.get("baseSalary")
    if isinstance(base, dict):
        value = base.get("value")
        currency = base.get("currency")
        if isinstance(value, dict):
            minimum = value.get("minValue")
            maximum = value.get("maxValue")
            unit = value.get("unitText")
            if minimum is not None or maximum is not None:
                amount = str(minimum) if minimum == maximum else "%s - %s" % (minimum, maximum)
                return " ".join(str(item) for item in (currency, amount, unit) if item)
        elif value is not None:
            return " ".join(str(item) for item in (currency, value) if item)

    if not state.get("compensationPublic"):
        return None
    minimum = state.get("compensationAmountMinCents")
    maximum = state.get("compensationAmountMaxCents")
    if minimum is None and maximum is None:
        return None
    minimum = minimum / 100 if minimum is not None else None
    maximum = maximum / 100 if maximum is not None else None
    amount = str(minimum) if minimum == maximum else "%s - %s" % (minimum, maximum)
    period = state.get("compensationPeriod")
    if period == "period_not_defined":
        period = None
    return " ".join(str(item) for item in (state.get("compensationCurrency"), amount, period) if item)


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    urls = re.findall(r"<loc>\s*(https://jobs\.framework\.ventures/companies/[^<]+/jobs/[^<]+)\s*</loc>", sitemap, re.I)

    keywords = (
        "engineer", "engineering", "developer", "backend", "protocol",
        "infrastructure", "devops", "software", "security", "blockchain",
    )
    selected = []
    for item in dict.fromkeys(html.unescape(item) for item in urls):
        slug = item.rsplit("/", 1)[-1].lower()
        if any(keyword in slug for keyword in keywords):
            selected.append(item)
        if len(selected) >= 6:
            break

    for url in selected:
        page = ctx.fetch(url)
        raw_ld = _script(page, r"type=[\"']application/ld\+json[\"']")
        if not raw_ld:
            continue
        try:
            ld = _jobposting(json.loads(raw_ld))
        except (TypeError, ValueError):
            continue
        if not ld:
            continue

        state = {}
        raw_next = _script(page, r"id=[\"']__NEXT_DATA__[\"']")
        if raw_next:
            try:
                next_data = json.loads(raw_next)
                state = next_data["props"]["pageProps"]["initialState"]["jobs"]["currentJob"] or {}
            except (KeyError, TypeError, ValueError):
                state = {}

        if state and (state.get("status") not in (None, "active") or state.get("visibility") not in (None, "visible")):
            continue

        organization = ld.get("hiringOrganization") or {}
        company = organization.get("name") if isinstance(organization, dict) else organization
        if not company and isinstance(state.get("organization"), dict):
            company = state["organization"].get("name")

        locations = []
        for item in state.get("locations") or []:
            name = item.get("name") if isinstance(item, dict) else item
            if name and name not in locations:
                locations.append(name)
        if not locations:
            places = ld.get("jobLocation") or []
            if not isinstance(places, list):
                places = [places]
            for place in places:
                name = _location_name(place)
                if name and name not in locations:
                    locations.append(name)
        if not locations and ld.get("jobLocationType") == "TELECOMMUTE":
            locations.append("Remote")

        description = _text(ld.get("description"))
        title = ld.get("title") or state.get("title")
        if not title or not company or not locations or not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        employment = ld.get("employmentType") or state.get("employmentTypes")
        if isinstance(employment, list):
            employment = ", ".join(str(item) for item in employment if item) or None
        if employment:
            job["employment_type"] = employment

        posted_at = ld.get("datePosted") or state.get("postedAt")
        expires_at = ld.get("validThrough") or state.get("expiresAt")
        if posted_at:
            job["posted_at"] = posted_at
        if expires_at:
            job["expires_at"] = expires_at

        external_id = state.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)
        apply_url = state.get("applicationPath") or state.get("url")
        if apply_url:
            job["apply_url"] = apply_url
        salary = _salary(ld, state)
        if salary:
            job["salary"] = salary
        if ld.get("jobLocationType") == "TELECOMMUTE":
            job["workplace_type"] = "remote"

        yield job
