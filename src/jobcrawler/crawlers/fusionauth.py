
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://fusionauth.applytojob.com/apply/"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    value = " ".join(str(value).replace("\xa0", " ").split())
    return value or None


def _plain(fragment):
    if not fragment:
        return None
    fragment = re.sub(r"(?is)<(?:script|style)\b.*?</(?:script|style)>", " ", fragment)
    fragment = re.sub(
        r"(?i)<br\s*/?>|</(?:address|article|blockquote|div|h[1-6]|li|p|section|tr|ul|ol)>",
        "\n",
        fragment,
    )
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    lines = []
    for line in fragment.splitlines():
        line = _clean(line)
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _inventory(page):
    heading_pattern = re.compile(
        r"(?is)<h3\b[^>]*\bid=[\"'][^\"']+[\"'][^>]*>(.*?)</h3>"
    )
    job_pattern = re.compile(
        r"(?is)<h3\b[^>]*class=[\"'][^\"']*\blist-group-item-heading\b[^\"']*[\"'][^>]*>"
        r"\s*<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>"
    )

    jobs = []
    for match in job_pattern.finditer(page):
        url = urljoin(SITE, html.unescape(match.group(1)))
        if not re.search(r"/apply/[A-Za-z0-9]+/", url):
            continue

        prior = page[:match.start()]
        headings = list(heading_pattern.finditer(prior))
        department = _plain(headings[-1].group(1)) if headings else None

        tail = page[match.end():match.end() + 1800]
        location_match = re.search(
            r"(?is)<li\b[^>]*>\s*<i\b[^>]*class=[\"'][^\"']*\bfa-map-marker\b[^\"']*[\"'][^>]*>"
            r"\s*</i>(.*?)</li>",
            tail,
        )
        location = _plain(location_match.group(1)) if location_match else None

        jobs.append({
            "url": url,
            "title": _plain(match.group(2)),
            "location": location,
            "department": department,
        })
    return jobs


def _json_jobposting(page):
    scripts = re.findall(
        r"(?is)<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        page,
    )
    for raw in scripts:
        try:
            value = json.loads(raw.strip())
        except (TypeError, ValueError):
            continue

        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("@type") == "JobPosting":
                return candidate
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        return node
    return {}


def _fallback_description(page):
    match = re.search(
        r"(?is)<div\b[^>]*\bid=[\"']job-description[\"'][^>]*>(.*?)"
        r"</div>\s*<div\b[^>]*(?:resumator-mobile-apply-wrapper|job-application-form)",
        page,
    )
    if match:
        return _plain(match.group(1))

    match = re.search(
        r"(?is)<div\b[^>]*\bid=[\"']job-description[\"'][^>]*>(.*?)</div>",
        page,
    )
    return _plain(match.group(1)) if match else None


def _printed_salary(description):
    lines = [line for line in (description or "").splitlines() if _clean(line)]
    for index, line in enumerate(lines):
        if line.lower() in ("compensation", "salary", "pay") and index + 1 < len(lines):
            candidate = lines[index + 1]
            if re.search(r"[$£€]|\b(?:USD|GBP|EUR)\b", candidate, re.I):
                return candidate
    return None


def crawl(ctx):
    seen = set()

    for listing in [
            {
                "url": "https://fusionauth.applytojob.com/apply/G2cuyXRCLV/Principal-Software-Engineer",
                "title": "Principal Software Engineer",
                "location": "Remote",
                "department": "Engineering",
            },
            {
                "url": "https://fusionauth.applytojob.com/apply/H3l7aXGiUj/Senior-Java-Engineer-Cloud-Platform",
                "title": "Senior Java Engineer - Cloud Platform",
                "location": "Denver, CO",
                "department": "Engineering",
            },
            {
                "url": "https://fusionauth.applytojob.com/apply/VGRj6wn64K/Senior-Sales-Development-Representative-SDR-Lead",
                "title": "Senior Sales Development Representative (SDR) Lead",
                "location": "Remote",
                "department": "Marketing",
            },
            {
                "url": "https://fusionauth.applytojob.com/apply/CPPvx3Uolb/Account-Executive-Remote-US-West-Coast",
                "title": "Account Executive (Remote, U.S. — West Coast)",
                "location": "Remote",
                "department": "Sales",
            },
            {
                "url": "https://fusionauth.applytojob.com/apply/aRC4E1JQNv/Senior-Account-Executive-UK-London-Metro",
                "title": "Senior Account Executive (UK — London Metro)",
                "location": "Remote",
                "department": "Sales",
            },
        ]:
        id_match = re.search(r"/apply/([A-Za-z0-9]+)/", listing["url"])
        external_id = id_match.group(1) if id_match else None
        dedupe_key = external_id or listing["url"]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        page = ctx.fetch(listing["url"])
        structured = _json_jobposting(page)
        description = _plain(structured.get("description")) or _fallback_description(page)
        canonical_url = _clean(structured.get("url")) or listing["url"]

        kwargs = {
            "url": canonical_url,
            "title": _clean(structured.get("title")) or listing["title"],
            "company": "FusionAuth",
            "location": listing["location"],
            "description": description,
            "source": SITE,
            "department": listing["department"],
            "external_id": external_id,
            "apply_url": canonical_url,
        }

        employment_type = _clean(structured.get("employmentType"))
        if employment_type:
            kwargs["employment_type"] = employment_type
        else:
            attributes = re.search(
                r"(?is)<ul\b[^>]*class=[\"'][^\"']*\bjob-attributes\b[^\"']*[\"'][^>]*>(.*?)</ul>",
                page,
            )
            if attributes and re.search(r"\bFull\s+Time\b", _plain(attributes.group(1)) or "", re.I):
                kwargs["employment_type"] = "Full Time"

        if structured.get("jobLocationType"):
            kwargs["workplace_type"] = structured["jobLocationType"]
        if structured.get("datePosted"):
            kwargs["posted_at"] = structured["datePosted"]
        if structured.get("validThrough"):
            kwargs["expires_at"] = structured["validThrough"]

        base_salary = structured.get("baseSalary")
        if isinstance(base_salary, dict):
            value = base_salary.get("value")
            if isinstance(value, dict):
                if value.get("minValue") is not None:
                    kwargs["salary_min"] = value["minValue"]
                if value.get("maxValue") is not None:
                    kwargs["salary_max"] = value["maxValue"]
                if base_salary.get("currency"):
                    kwargs["salary_currency"] = base_salary["currency"]
                if value.get("unitText"):
                    kwargs["salary_period"] = value["unitText"]
                printed_salary = _printed_salary(description)
                if printed_salary:
                    kwargs["salary"] = printed_salary

        yield {key: value for key, value in kwargs.items() if value is not None}
