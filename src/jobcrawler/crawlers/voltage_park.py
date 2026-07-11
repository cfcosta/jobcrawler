"""Crawler for Voltage Park's careers board (Kula-hosted, imported from Ashby)."""

import html
import json
import re

SITE = "https://careers.kula.ai/api/internal/ats_job_posts?accountName=voltagepark&type=ats_job_post.index&items=99&page=1"
EXTRA_HOSTS = []

COMPANY = "Voltage Park"
_DETAIL = "https://careers.kula.ai/voltagepark/{id}"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_WORKPLACE = {"office": "On-Site", "remote": "Remote", "hybrid": "Hybrid"}
_EMPLOYMENT = {
    "full_time": "Full Time",
    "part_time": "Part Time",
    "contract": "Contract",
    "internship": "Internship",
    "temporary": "Temporary",
    "seasonal": "Seasonal",
}


def _strip_tags(html_text):
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_amount(value):
    return f"{int(value):,}" if value == int(value) else f"{value:,.2f}"


def crawl(ctx):
    """Yield one Job dict per listed open role from Voltage Park's Kula board."""
    page = 1
    seen = set()
    while True:
        url = SITE if page == 1 else SITE.replace("page=1", f"page={page}")
        data = json.loads(ctx.fetch(url))
        jobs = data.get("data") or []
        for job in jobs:
            if job.get("listed") is False or job.get("is_confidential") is True:
                continue

            job_id = job.get("id")
            title = job.get("title")
            if not (job_id and title):
                continue
            if job_id in seen:
                continue
            seen.add(job_id)

            ats = job.get("ats_job") or {}
            description = _strip_tags(ats.get("job_description"))
            if not description:
                continue

            offices = ats.get("offices") or []
            locations = []
            for office in offices:
                if not isinstance(office, dict):
                    continue
                name = office.get("location") or office.get("name")
                if name and name not in locations:
                    locations.append(name)

            workplace_type = _WORKPLACE.get(ats.get("workplace"))
            location = locations[0] if locations else workplace_type
            if not location:
                continue

            out = {
                "url": _DETAIL.format(id=job_id),
                "title": title,
                "company": COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
            }

            if len(locations) > 1:
                out["locations"] = locations

            department = (ats.get("ats_department") or {}).get("name")
            if department:
                out["department"] = department

            employment_type = _EMPLOYMENT.get(ats.get("employment_type"))
            if employment_type:
                out["employment_type"] = employment_type

            if workplace_type:
                out["workplace_type"] = workplace_type

            comp = (ats.get("compensation") or {}).get("base_salary") or {}
            salary_min = _num(comp.get("min_amount"))
            salary_max = _num(comp.get("max_amount"))
            currency = comp.get("currency")
            interval = comp.get("interval")
            symbol = comp.get("currency_symbol")
            if salary_min is not None:
                out["salary_min"] = salary_min
            if salary_max is not None:
                out["salary_max"] = salary_max
            if currency:
                out["salary_currency"] = currency
            if interval:
                out["salary_period"] = interval
            if salary_min is not None or salary_max is not None:
                prefix = symbol or (f"{currency} " if currency else "")
                if salary_min is not None and salary_max is not None:
                    summary = f"{prefix}{_fmt_amount(salary_min)} - {prefix}{_fmt_amount(salary_max)}"
                else:
                    amount = salary_min if salary_min is not None else salary_max
                    summary = f"{prefix}{_fmt_amount(amount)}"
                if interval:
                    summary = f"{summary} / {interval}"
                out["salary"] = summary

            yield out

        meta = data.get("meta") or {}
        pages = meta.get("pages")
        if not isinstance(pages, int) or page >= pages:
            break
        page += 1
