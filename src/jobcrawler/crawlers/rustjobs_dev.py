
import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

SITE = "https://rustjobs.dev/"
EXTRA_HOSTS = []
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36"
}

def text(html):
    html = re.sub("(?is)<(?:script|style)\\b.*?</(?:script|style)>", " ", html)
    html = re.sub("(?i)<br\\s*/?>", "\n", html)
    html = re.sub("(?i)</(?:p|li|h[1-6]|div|section)>", "\n", html)
    html = re.sub("(?s)<[^>]+>", " ", html)
    lines = []
    for line in unescape(html).splitlines():
        line = re.sub("[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def jobposting(body):
    blocks = re.findall(
        "(?is)<script\\b[^>]*type\\s*=\\s*[\"']application/ld\\+json[\"'][^>]*>(.*?)</script>",
        body
    )
    for block in blocks:
        try:
            root = json.loads(unescape(block))
        except (ValueError, TypeError):
            continue
        pending = root if isinstance(root, list) else [root]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                kind = value.get("@type")
                if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
                    return value
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        pending.append(child)

def address_text(value):
    if not isinstance(value, dict):
        return str(value).strip() if value else None
    address = value.get("address", value)
    if not isinstance(address, dict):
        return str(address).strip() if address else None
    parts = []
    for key in ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"):
        part = address.get(key)
        if isinstance(part, dict):
            part = part.get("name")
        if part and str(part) not in parts:
            parts.append(str(part))
    return ", ".join(parts) or None

def locations_from(data):
    values = data.get("jobLocation")
    if values and not isinstance(values, list):
        values = [values]
    locations = []
    for value in values or []:
        location = address_text(value)
        if location and location not in locations:
            locations.append(location)

    if not locations:
        requirements = data.get("applicantLocationRequirements")
        if requirements and not isinstance(requirements, list):
            requirements = [requirements]
        for value in requirements or []:
            if isinstance(value, dict):
                location = value.get("name") or address_text(value)
            else:
                location = str(value)
            if location and location not in locations:
                locations.append(location)

    display = ", ".join(locations)
    remote_type = data.get("jobLocationType")
    if remote_type and not display:
        display = str(remote_type).replace("TELECOMMUTE", "Remote")
    return display or None, locations

def number(value):
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    normalized = re.sub("[, _]", "", value.strip())
    if not re.match("^-?[0-9]+(?:\\.[0-9]+)?$", normalized):
        return None
    return float(normalized)

def salary_fields(data):
    base = data.get("baseSalary")
    if not isinstance(base, dict):
        return {}
    currency = base.get("currency")
    value = base.get("value")
    period = None
    minimum = None
    maximum = None
    if isinstance(value, dict):
        minimum = number(value.get("minValue"))
        maximum = number(value.get("maxValue"))
        period = value.get("unitText")
        exact = number(value.get("value"))
        if exact is not None:
            minimum = exact
            maximum = exact
    else:
        exact = number(value)
        if exact is not None:
            minimum = exact
            maximum = exact

    result = {
        "salary_min": minimum,
        "salary_max": maximum,
        "salary_currency": currency,
        "salary_period": period
    }
    return {key: value for key, value in result.items() if value is not None}

def apply_link(body, page_url):
    for match in re.finditer(
        "(?is)<a\\b([^>]*?\\bhref\\s*=\\s*[\"']([^\"']+)[\"'][^>]*)>(.*?)</a>",
        body
    ):
        href = unescape(match.group(2)).strip()
        label = text(match.group(3))
        attrs = match.group(1)
        if re.search("(?i)\\bapply(?: now| for| here)?\\b", label + " " + attrs):
            if href.startswith(("http://", "https://", "mailto:")):
                return href
            return urljoin(page_url, href)

def identifier(data):
    value = data.get("identifier")
    if isinstance(value, dict):
        return value.get("value") or value.get("name")
    return value

def crawl(ctx):
    home = ctx.fetch(SITE, headers=UA)
    hrefs = re.findall(
        "(?is)<a\\b[^>]*?\\bhref\\s*=\\s*[\"']([^\"']+)[\"']",
        home
    )
    urls = []
    for href in hrefs:
        url = urljoin(SITE, unescape(href)).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.netloc == "rustjobs.dev" and re.match("^/jobs/[^/]+/?$", parsed.path):
            if url not in urls:
                urls.append(url)

    for url in urls:
        body = ctx.fetch(url, headers=UA)
        data = jobposting(body)
        if not data:
            continue

        organization = data.get("hiringOrganization")
        if isinstance(organization, dict):
            company = organization.get("name")
        else:
            company = organization

        location, locations = locations_from(data)
        description = text(str(data.get("description") or ""))
        record = {
            "url": url,
            "title": data.get("title"),
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "posted_at": data.get("datePosted"),
            "expires_at": data.get("validThrough"),
            "employment_type": data.get("employmentType"),
            "external_id": identifier(data),
            "apply_url": apply_link(body, url)
        }
        if len(locations) > 1:
            record["locations"] = locations
        if data.get("jobLocationType"):
            record["workplace_type"] = data.get("jobLocationType")
        record.update(salary_fields(data))
        yield {key: value for key, value in record.items() if value not in (None, "")}
