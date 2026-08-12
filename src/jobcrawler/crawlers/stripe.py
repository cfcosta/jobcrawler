
import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job
from urllib.parse import urljoin

SITE = "https://stripe.com/jobs/search"
EXTRA_HOSTS = []

SITEMAP = "https://stripe.com/sitemap/partition-0.xml"


def text_content(fragment):
    if not fragment:
        return ""
    value = re.sub(
        r"<(?:br|/p|/li|/h[1-6]|/div|/section|/article|/tr|/blockquote)\s*/?>",
        "\n",
        str(fragment),
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def jsonld_job(page):
    scripts = re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    for payload in scripts:
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            try:
                value = json.loads(unescape(payload))
            except (TypeError, ValueError):
                continue

        candidates = value if isinstance(value, list) else [value]
        if isinstance(value, dict) and isinstance(value.get("@graph"), list):
            candidates += value["@graph"]

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            kinds = candidate.get("@type")
            if kinds == "JobPosting" or (
                isinstance(kinds, list) and "JobPosting" in kinds
            ):
                return candidate
    return None


def sidebar_facts(page):
    facts = {}
    pattern = (
        r'<dt\b[^>]*careers-listing-details__fact-label[^>]*>'
        r'(.*?)</dt>\s*'
        r'<dd\b[^>]*careers-listing-details__fact-value[^>]*>'
        r'(.*?)</dd>'
    )
    for label_html, value_html in re.findall(pattern, page, re.I | re.S):
        label = text_content(label_html)
        value = text_content(value_html)
        if label and value:
            facts[label.casefold()] = value
    return facts


def structured_locations(posting):
    locations = []
    raw_locations = posting.get("jobLocation")
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]
    if not isinstance(raw_locations, list):
        return locations

    for place in raw_locations:
        if not isinstance(place, dict):
            continue
        address = place.get("address")
        if isinstance(address, str):
            value = address.strip()
        elif isinstance(address, dict):
            parts = []
            for key in (
                "streetAddress",
                "addressLocality",
                "addressRegion",
                "postalCode",
                "addressCountry",
            ):
                part = address.get(key)
                if isinstance(part, dict):
                    part = part.get("name")
                if part is not None and str(part).strip():
                    parts.append(str(part).strip())
            value = ", ".join(parts)
        else:
            value = ""

        if value and value not in locations:
            locations.append(value)
    return locations


def salary_text(description):
    match = re.search(
        r'((?:US[$ ]|CA[$ ]|A[$ ]|S[$ ]|€|£|\$)'
        r'\s?[\d,.]+\s*(?:-|–|—|to)\s*'
        r'(?:US[$ ]|CA[$ ]|A[$ ]|S[$ ]|€|£|\$)?\s?[\d,.]+)',
        description,
        re.I,
    )
    return match.group(1).strip() if match else None


def crawl(ctx):
    search_page = ctx.fetch(SITE)
    paths = re.findall(
        r'href=["\']([^"\']*/careers/listing/[^"\']+/\d+/?)["\']',
        search_page,
        re.I,
    )
    urls = []
    for path in paths:
        url = urljoin(SITE, unescape(path))
        if (
            re.match(
                r"^https://stripe\.com/careers/listing/[^/?#]+/\d+/?$",
                url,
                re.I,
            )
            and url not in urls
        ):
            urls.append(url)

    seen_ids = set()
    for discovered_url in urls:
        external_match = re.search(r"/(\d+)/?$", discovered_url)
        if not external_match:
            continue
        discovered_id = external_match.group(1)
        if discovered_id in seen_ids:
            continue

        page = ctx.fetch(discovered_url)
        posting = jsonld_job(page)
        if not posting:
            continue

        title = posting.get("title")
        description = text_content(posting.get("description", ""))
        canonical_url = posting.get("url") or discovered_url
        if not title or not description:
            continue
        if not re.match(
            r"^https://stripe\.com/careers/listing/", canonical_url, re.I
        ):
            canonical_url = discovered_url

        identifier = posting.get("identifier")
        external_id = discovered_id
        if isinstance(identifier, dict) and identifier.get("value") is not None:
            external_id = str(identifier["value"]).strip() or discovered_id
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        facts = sidebar_facts(page)
        locations = structured_locations(posting)
        location = (
            facts.get("office locations")
            or facts.get("remote locations")
            or facts.get("location")
        )
        if not location and locations:
            location = locations[0]
        if not location and posting.get("jobLocationType") == "TELECOMMUTE":
            location = "Remote"
        if not location:
            continue

        fields = {
            "url": canonical_url,
            "title": str(title).strip(),
            "company": "Stripe",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": external_id,
        }

        if locations:
            fields["locations"] = locations

        apply_match = re.search(
            r'<a\b[^>]*\bhref=["\']([^"\']*/careers/apply/[^"\']+)["\']',
            page,
            re.I,
        )
        if apply_match:
            fields["apply_url"] = urljoin(
                canonical_url, unescape(apply_match.group(1))
            )

        if posting.get("datePosted"):
            fields["posted_at"] = posting["datePosted"]

        employment_type = (
            facts.get("employment type") or posting.get("employmentType")
        )
        if employment_type:
            fields["employment_type"] = employment_type

        department = facts.get("team")
        if department:
            fields["department"] = department

        if posting.get("jobLocationType"):
            fields["workplace_type"] = posting["jobLocationType"]

        base_salary = posting.get("baseSalary")
        if isinstance(base_salary, dict):
            currency = base_salary.get("currency")
            value = base_salary.get("value")
            if isinstance(value, dict):
                if value.get("minValue") is not None:
                    fields["salary_min"] = value["minValue"]
                if value.get("maxValue") is not None:
                    fields["salary_max"] = value["maxValue"]
                if value.get("unitText"):
                    fields["salary_period"] = value["unitText"]
            if currency:
                fields["salary_currency"] = currency

            printed_salary = salary_text(description)
            if printed_salary:
                fields["salary"] = printed_salary

        yield Job(**fields)
