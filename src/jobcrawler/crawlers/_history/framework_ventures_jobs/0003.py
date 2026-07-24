
import json
import re
from html import unescape

SITE = "https://jobs.framework.ventures/"
EXTRA_HOSTS = []


def clean_html(value):
    text = unescape(value or "")
    text = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</h[1-6]\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def named_values(values):
    result = []
    for value in values or []:
        if isinstance(value, str):
            name = value.strip()
        elif isinstance(value, dict):
            name = (value.get("name") or value.get("label") or "").strip()
        else:
            name = ""
        if name and name not in result:
            result.append(name)
    return result


def explicit_location(job, description):
    locations = named_values(job.get("locations"))
    if locations:
        return locations[0], locations

    # Some imported postings state location only in the full description.
    patterns = [
        r"(?i)\b(?:job\s+)?location\s*:\s*(.{1,160}?)(?=\s+(?:licensure|salary|compensation|department|reports?\s+to|employment\s+type|about|role)\s*:|$)",
        r"(?im)^\s*location\s*[-|]\s*([^\n]{1,160})",
        r"(?im)^\s*work\s+location\s*:\s*([^\n]{1,160})",
    ]
    for pattern in patterns:
        match = re.search(pattern, description)
        if match:
            value = match.group(1).strip(" -|.;")
            value = re.split(
                r"\s+(?=(?:salary|compensation|licensure|department|reports?\s+to|employment\s+type|about|role)\s*:)",
                value,
                maxsplit=1,
                flags=re.I,
            )[0].strip(" -|.;")
            if value:
                return value, None

    opening = description[:1800]
    if re.search(r"(?i)\b(?:fully|100%)\s+remote\b|\bremote(?:[- ]first)?\s+(?:role|position|job|work)\b", opening):
        return "Remote", None
    return None, None


def salary_text(description):
    match = re.search(
        r"(?im)^\s*(salary|compensation|pay range)\s*:\s*([^\n]{1,180})",
        description,
    )
    if not match:
        return None
    value = (match.group(1) + ": " + match.group(2)).strip()
    return value if re.search(r"[$€£]|\b[A-Z]{3}\b", value) else None


def crawl(ctx):
    sitemap = ctx.fetch(SITE + "sitemap.xml")
    urls = re.findall(
        r"<loc>\s*(https://jobs\.framework\.ventures/companies/[^<\s]+/jobs/[^<\s]+)\s*</loc>",
        sitemap,
        re.I,
    )

    selected = []
    seen = set()
    for raw_url in urls:
        url = unescape(raw_url)
        if url in seen:
            continue
        seen.add(url)
        selected.append(url)
        if len(selected) >= 3:
            break

    for url in selected:
        page = ctx.fetch(url)
        match = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            page,
            re.I | re.S,
        )
        if not match:
            continue

        try:
            data = json.loads(match.group(1))
            job = data["props"]["pageProps"]["initialState"]["jobs"]["currentJob"]
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(job, dict):
            continue
        if job.get("status") not in (None, "active"):
            continue
        if job.get("visibility") not in (None, "visible"):
            continue
        if job.get("closedAt") or job.get("deactivatedAt"):
            continue

        title = job.get("title")
        company = (job.get("organization") or {}).get("name")
        description = clean_html(job.get("description"))
        location, locations = explicit_location(job, description)
        if not title or not company or not description or not location:
            continue

        item = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }
        if locations:
            item["locations"] = locations

        external_id = job.get("id")
        if external_id is not None:
            item["external_id"] = str(external_id)
        if job.get("postedAt"):
            item["posted_at"] = job["postedAt"]
        if job.get("expiresAt"):
            item["expires_at"] = job["expiresAt"]
        if job.get("url"):
            item["apply_url"] = job["url"]

        employment_types = named_values(job.get("employmentTypes"))
        if employment_types:
            item["employment_type"] = ", ".join(employment_types)
        departments = named_values(job.get("jobFunctions"))
        if departments:
            item["department"] = ", ".join(departments)

        if job.get("compensationPublic"):
            minimum = job.get("compensationAmountMinCents")
            maximum = job.get("compensationAmountMaxCents")
            currency = job.get("compensationCurrency")
            period = job.get("compensationPeriod")
            if minimum is not None:
                item["salary_min"] = minimum / 100
            if maximum is not None:
                item["salary_max"] = maximum / 100
            if currency:
                item["salary_currency"] = currency
            if period and period != "period_not_defined":
                item["salary_period"] = period
            printed_salary = salary_text(description)
            if printed_salary:
                item["salary"] = printed_salary

        yield item
