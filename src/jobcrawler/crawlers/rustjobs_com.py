import html
import re
from datetime import datetime
from urllib.parse import urljoin

SITE = "https://www.rustjobs.com/jobs.html"
EXTRA_HOSTS = []

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _strip_tags(value):
    value = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", value or "")
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    return _clean_text(value)


def _html_to_text(fragment):
    fragment = re.sub(
        r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", fragment or ""
    )
    fragment = re.sub(
        r"(?is)<a\s+[^>]*class=[\"']anchor[\"'][^>]*>.*?</a>", "", fragment
    )
    # Preserve posting section boundaries and list structure.
    fragment = re.sub(r"(?i)<\s*(h1|h2|h3|h4)[^>]*>", "\n\n", fragment)
    fragment = re.sub(r"(?i)<\s*/\s*(h1|h2|h3|h4)\s*>", "\n", fragment)
    fragment = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", fragment)
    fragment = re.sub(r"(?i)<\s*/\s*p\s*>", "\n\n", fragment)
    fragment = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"(?i)<\s*/\s*(ul|ol)\s*>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _content_fragment(page):
    match = re.search(
        r'(?is)<div\s+id=["\']content["\'][^>]*>(.*?)(?:<div\s+id=["\']footer["\']|</body>)',
        page or "",
    )
    if match:
        return match.group(1)
    return page or ""


def _detail_description(page):
    fragment = _content_fragment(page)
    fragment = re.sub(
        r"(?is)<p>\s*<a\s+class=[\"']button[\"'][^>]*>.*?</a>\s*</p>", " ", fragment
    )
    return _html_to_text(fragment)


def _parse_date(label):
    text = _strip_tags(label).lower()
    match = re.search(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b", text)
    if not match:
        return None
    month = _MONTHS.get(match.group(1))
    if not month:
        return None
    day = int(match.group(2))
    year = int(match.group(3))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _parse_job_block(block, posted_at):
    link = re.search(
        r'(?is)<a\s+href=["\']([^"\']*/jobs/[^"\']+)["\'][^>]*>(.*?)</a>', block
    )
    if not link:
        return None
    fields = re.findall(r"(?is)<li>(.*?)</li>", block)
    company = None
    location = None
    salary = None
    if len(fields) > 1:
        company_location = _strip_tags(fields[1])
        if ":" in company_location:
            company, location = [
                part.strip() for part in company_location.split(":", 1)
            ]
        else:
            location = company_location
    if len(fields) > 2:
        salary = _strip_tags(fields[2])
    item = {
        "url": urljoin(SITE, html.unescape(link.group(1))),
        "title": _strip_tags(link.group(2)),
        "company": company,
        "location": location,
    }
    if salary:
        item["salary"] = salary
    if posted_at:
        item["posted_at"] = posted_at
    return item


def _parse_listing(page):
    jobs = []
    current_date = None
    parts = re.split(r"(?is)(<h2\b.*?</h2>)", page or "")
    for part in parts:
        if re.match(r"(?is)<h2\b", part or ""):
            label = _strip_tags(part)
            parsed = _parse_date(label)
            if parsed:
                current_date = parsed
            continue
        for block in re.findall(
            r"(?is)<li>\s*<ul>\s*(.*?)\s*</ul>\s*</li>", part or ""
        ):
            item = _parse_job_block(block, current_date)
            if item:
                jobs.append(item)
    return jobs


def crawl(ctx):
    listing = ctx.fetch(SITE)
    for item in _parse_listing(listing):
        page = ctx.fetch(item["url"])
        description = _detail_description(page)
        job = {
            "url": item["url"],
            "title": item["title"],
            "company": item.get("company"),
            "location": item.get("location"),
            "description": description,
            "source": SITE,
        }
        for key in ("salary", "posted_at"):
            if item.get(key):
                job[key] = item[key]
        yield job
