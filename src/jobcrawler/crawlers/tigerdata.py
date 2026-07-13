
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/tigerdata?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _geo_string(location, address):
    """Best §4.12 location string for an Ashby location slot.

    Ashby's display ``location`` can carry employment-type noise ('US
    Full-time'), which the geo parser mis-reads as a city ('Full-time
    Full-time'); that bogus city then blocks the remote-fence promotion for the
    whole posting, so a remote-from-Brazil role reads as a plain Brazil office.
    When the structured ``address`` is country-only, the clean ``addressCountry``
    name parses correctly (and, on a remote role, becomes a remote fence). When
    it carries a city/region the display string is the richer source, so keep it.
    """
    location = _clean(location)
    postal = {}
    if isinstance(address, dict):
        postal = address.get("postalAddress") or {}
    country = _clean(postal.get("addressCountry"))
    has_locale = _clean(postal.get("addressLocality")) or _clean(
        postal.get("addressRegion")
    )
    if country and not has_locale:
        return country
    return location


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        description = _clean(job.get("descriptionPlain"))
        url = _clean(job.get("jobUrl"))
        location = _geo_string(job.get("location"), job.get("address"))
        if not (title and description and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Tiger Data",
            "location": location,
            "description": description,
            "source": SITE,
        }

        # Ashby carries extra offices/remote scopes in secondaryLocations; keep
        # them so multi-location roles (e.g. US + Brazil + Spain + Remote) project
        # every §4.12 place/fence rather than only the primary location. Each slot
        # resolves through _geo_string so a noisy display string ('Spain
        # Full-time') falls back to its clean structured country.
        secondary = job.get("secondaryLocations") or []
        if secondary:
            locations = [location]
            for entry in secondary:
                if isinstance(entry, dict):
                    value = _geo_string(entry.get("location"), entry.get("address"))
                else:
                    value = _clean(entry)
                if value and value not in locations:
                    locations.append(value)
            if len(locations) > 1:
                out["locations"] = locations

        mappings = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for src, dest in mappings.items():
            value = _clean(job.get(src))
            if value is not None:
                out[dest] = value

        comp = job.get("compensation") or {}
        components = comp.get("summaryComponents") or []
        salary_parts = []
        for component in components:
            if not isinstance(component, dict):
                continue
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = _clean(component.get("currencyCode"))
            interval = _clean(component.get("interval"))
            if min_value is None and max_value is None:
                continue
            salary_part = {
                "min_value": min_value,
                "max_value": max_value,
            }
            if currency is not None:
                salary_part["currency"] = currency
            if interval is not None:
                salary_part["interval"] = interval
            salary_parts.append(salary_part)
        if salary_parts:
            out["salary"] = salary_parts

        yield out
