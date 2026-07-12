
import html
import json
import re
from urllib.parse import urljoin, urlparse


SITE = "https://jobs.earthly.dev"
EXTRA_HOSTS = []


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(?:br|hr)\s*/?>|</(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote)>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def postings(value):
    if isinstance(value, list):
        for item in value:
            for posting in postings(item):
                yield posting
    elif isinstance(value, dict):
        kinds = value.get("@type")
        if kinds == "JobPosting" or (
            isinstance(kinds, list) and "JobPosting" in kinds
        ):
            yield value
        if "@graph" in value:
            for posting in postings(value["@graph"]):
                yield posting


def place_text(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return None

    address = value.get("address")
    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, dict):
        return None

    parts = []
    for key in (
        "streetAddress",
        "addressLocality",
        "addressRegion",
        "postalCode",
        "addressCountry",
    ):
        part = address.get(key)
        if part is not None:
            part = str(part).strip()
            if part and part not in parts:
                parts.append(part)
    return ", ".join(parts) if parts else None


def normalized_date(value):
    if not value:
        return None
    value = str(value).strip()
    if value.endswith(" UTC"):
        value = value[:-4] + "+00:00"
    return value


def make_job(data, fallback_url):
    organization = data.get("hiringOrganization")
    company = (
        organization.get("name")
        if isinstance(organization, dict)
        else None
    )

    remote = str(data.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    location = place_text(data.get("jobLocation"))
    if not location and remote:
        location = "Remote"

    kwargs = {
        "url": data.get("url") or fallback_url,
        "title": data.get("title"),
        "company": company,
        "location": location,
        "description": clean_text(data.get("description")),
        "source": SITE,
    }

    identifier = data.get("identifier")
    if isinstance(identifier, dict) and identifier.get("value") is not None:
        kwargs["external_id"] = str(identifier["value"])

    if data.get("datePosted"):
        kwargs["posted_at"] = normalized_date(data["datePosted"])
    if data.get("validThrough"):
        kwargs["expires_at"] = normalized_date(data["validThrough"])
    if data.get("employmentType"):
        kwargs["employment_type"] = data["employmentType"]
    if remote:
        kwargs["workplace_type"] = "remote"

    salary = data.get("baseSalary")
    if isinstance(salary, dict):
        amount = salary.get("value")
        if isinstance(amount, dict):
            if amount.get("minValue") is not None:
                kwargs["salary_min"] = amount["minValue"]
            if amount.get("maxValue") is not None:
                kwargs["salary_max"] = amount["maxValue"]
            if amount.get("unitText"):
                kwargs["salary_period"] = amount["unitText"]
        if salary.get("currency"):
            kwargs["salary_currency"] = salary["currency"]

    return kwargs


def crawl(ctx):
    board_html = ctx.fetch(SITE)
    hrefs = re.findall(
        r'''(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']''',
        board_html,
    )

    seen = set()
    for href in hrefs:
        url = urljoin(SITE, html.unescape(href))
        parsed = urlparse(url)
        if parsed.netloc != "jobs.earthly.dev":
            continue
        if not re.fullmatch(r"/\d+", parsed.path):
            continue

        url = parsed.scheme + "://" + parsed.netloc + parsed.path
        if url in seen:
            continue
        seen.add(url)

        page_html = ctx.fetch(url)
        scripts = re.findall(
            r'''(?is)<script\b[^>]*\btype\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script\s*>''',
            page_html,
        )
        for raw in scripts:
            try:
                payload = json.loads(html.unescape(raw))
            except (TypeError, ValueError):
                continue

            found = False
            for data in postings(payload):
                yield make_job(data, url)
                found = True
                break
            if found:
                break
