
import json
import re
from html import unescape

SITE = "https://jobs.ashbyhq.com/chainlink-labs"
EXTRA_HOSTS = []

_APP_DATA_MARKER = "window.__appData = "


def _app_data(page):
    position = page.find(_APP_DATA_MARKER)
    if position < 0:
        raise ValueError("Ashby app data was not found")

    payload = page[position + len(_APP_DATA_MARKER):].lstrip()
    return json.JSONDecoder().raw_decode(payload)[0]


def _plain_text(value):
    text = value or ""
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    listing_page = ctx.fetch(SITE)
    listing_data = _app_data(listing_page)
    postings = listing_data.get("jobBoard", {}).get("jobPostings", [])

    seen = set()
    for summary in postings:
        posting_id = summary.get("id")
        if not posting_id or posting_id in seen or not summary.get("isListed", True):
            continue
        seen.add(posting_id)

        url = SITE + "/" + posting_id
        detail_page = ctx.fetch(url)
        detail = _app_data(detail_page).get("posting") or {}

        if not detail.get("isListed", True):
            continue

        title = (detail.get("title") or summary.get("title") or "").strip()
        location = detail.get("locationName") or summary.get("locationName")
        description = _plain_text(detail.get("descriptionHtml"))

        if not title or not location or not description:
            continue

        locations = []
        primary_location = summary.get("locationName")
        if primary_location:
            locations.append(primary_location)

        for secondary in summary.get("secondaryLocations", []):
            if isinstance(secondary, dict):
                name = secondary.get("locationName")
                if name and name not in locations:
                    locations.append(name)

        item = {
            "url": url,
            "title": title,
            "company": "Chainlink Labs",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting_id,
            "apply_url": url + "/application",
        }

        if locations:
            item["locations"] = locations

        department = detail.get("departmentName") or summary.get("departmentName")
        if department:
            item["department"] = department

        employment_type = detail.get("employmentType") or summary.get("employmentType")
        if employment_type:
            item["employment_type"] = employment_type

        workplace_type = detail.get("workplaceType") or summary.get("workplaceType")
        if workplace_type:
            item["workplace_type"] = workplace_type

        published = summary.get("publishedDate")
        if published:
            item["posted_at"] = published

        deadline = detail.get("applicationDeadline") or summary.get("applicationDeadline")
        if deadline:
            item["expires_at"] = deadline

        salary = (
            detail.get("scrapeableCompensationSalarySummary")
            or detail.get("compensationTierSummary")
            or summary.get("compensationTierSummary")
        )
        if salary:
            item["salary"] = salary

        yield item
