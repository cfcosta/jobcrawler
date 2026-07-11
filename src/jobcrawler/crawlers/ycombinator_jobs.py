
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://www.workatastartup.com/jobs/l/software-engineer"
EXTRA_HOSTS = []

BASE = "https://www.workatastartup.com"
ROLE_SLUGS = ["software-engineer"]
MAX_DETAILS = 8


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _strip_html(value):
    if not value:
        return None
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)\s*>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _data_page(text):
    m = re.search(r'data-page="([^"]+)"', text or "")
    if not m:
        return {}
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return {}


def _detail(ctx, jid):
    try:
        page = ctx.fetch(urljoin(BASE, "/jobs/%s.html" % jid))
    except Exception:
        return {}, {}, None
    props = (_data_page(page).get("props") or {})
    return props.get("job") or {}, props.get("company") or {}, _clean(props.get("applyUrl"))


def _description(detail_job, company, listing):
    parts = []

    desc = _strip_html(detail_job.get("descriptionHtml"))
    if desc:
        parts.append(desc)

    interview = _strip_html(detail_job.get("interviewProcessHtml"))
    if interview:
        parts.append("Interview process:\n" + interview)

    hiring = _strip_html(company.get("hiringDescriptionHtml"))
    if hiring:
        parts.append("About %s:\n%s" % (_clean(company.get("name")) or "the company", hiring))

    tech = _strip_html(company.get("techDescriptionHtml"))
    if tech:
        parts.append("Technology:\n" + tech)

    details = []
    for label, value in [
        ("Job type", detail_job.get("jobType") or listing.get("jobType")),
        ("Role type", listing.get("roleType")),
        ("Location", detail_job.get("location") or listing.get("location")),
        ("Salary", detail_job.get("salaryRange") or listing.get("salary")),
        ("Equity", detail_job.get("equityRange")),
        ("Experience", detail_job.get("minExperience")),
        ("Visa", detail_job.get("sponsorsVisa")),
        ("Skills", ", ".join(detail_job.get("skills") or [])),
        ("YC batch", company.get("batch") or listing.get("companyBatch")),
    ]:
        value = _clean(value)
        if value:
            details.append("%s: %s" % (label, value))
    if details:
        parts.append("\n".join(details))

    return "\n\n".join(p for p in parts if p).strip()


def crawl(ctx):
    seen = set()
    detail_count = 0

    for slug in ROLE_SLUGS:
        try:
            page = ctx.fetch(urljoin(BASE, "/jobs/l/%s.html" % slug))
        except Exception:
            continue

        props = (_data_page(page).get("props") or {})
        for listing in props.get("jobs") or []:
            if detail_count >= MAX_DETAILS:
                return

            jid = listing.get("id")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            detail_count += 1

            detail_job, company, detail_apply_url = _detail(ctx, jid)

            title = _clean(detail_job.get("title") or listing.get("title"))
            company_name = _clean(company.get("name") or listing.get("companyName"))
            location = _clean(detail_job.get("location") or listing.get("location") or company.get("location"))
            description = _description(detail_job, company, listing)
            if not (title and company_name and location and description):
                continue

            item = {
                "url": urljoin(BASE, "/jobs/%s.html" % jid),
                "title": title,
                "company": company_name,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(jid),
            }

            apply_url = _clean(detail_apply_url or listing.get("applyUrl"))
            if apply_url:
                item["apply_url"] = apply_url

            employment_type = _clean(detail_job.get("jobType") or listing.get("jobType"))
            if employment_type:
                item["employment_type"] = employment_type

            department = _clean(listing.get("roleType"))
            if department:
                item["department"] = department

            salary = _clean(detail_job.get("salaryRange") or listing.get("salary"))
            if salary:
                item["salary"] = salary

            yield item
