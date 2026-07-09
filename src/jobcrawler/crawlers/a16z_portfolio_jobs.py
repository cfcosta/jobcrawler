import json
import re
from html import unescape
from urllib.parse import quote

SITE = "https://jobs.a16z.com/"
EXTRA_HOSTS = []

HOSTED_JOBS = [
    {
        "company_id": "IYK",
        "company_slug": "iyk",
        "job_id": "810f42b8-9475-47ad-97b5-5850017e70ed",
    },
    {
        "company_id": "Angle Protocol",
        "company_slug": "angle-protocol",
        "job_id": "cdf9fb99-01f5-4d51-a5bc-31b0871263a3",
    },
]


def _clean(value):
    if value is None:
        return None
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _labels(values):
    labels = []
    for item in values or []:
        if isinstance(item, dict):
            value = item.get("label") or item.get("value") or item.get("id")
        else:
            value = item
        if value:
            labels.append(str(value))
    return labels


def _positive_amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None


def _salary_fields(value):
    """Getro flattens {minValue, maxValue, currency, period} into text; keep the
    verbatim joined text AND the structured quartet (zero/absent bounds omitted,
    period passed as stated — the host maps the wording)."""
    if not isinstance(value, dict):
        return {}

    currency = value.get("currency")
    if isinstance(currency, dict):
        currency = currency.get("value") or currency.get("label")

    period = value.get("period")
    if isinstance(period, dict):
        period = period.get("value") or period.get("label")

    lo = value.get("minValue")
    hi = value.get("maxValue")
    bits = []

    if lo is not None and hi is not None:
        bits.append("%s-%s" % (lo, hi))
    elif lo is not None:
        bits.append(str(lo))
    elif hi is not None:
        bits.append(str(hi))

    if currency:
        bits.append(str(currency))
    if period:
        bits.append("per " + str(period))

    text = " ".join(bits)
    if not text:
        return {}

    fields = {"salary": text}
    lo_num = _positive_amount(lo)
    hi_num = _positive_amount(hi)
    if lo_num is not None:
        fields["salary_min"] = lo_num
    if hi_num is not None:
        fields["salary_max"] = hi_num
    if lo_num is None and hi_num is None:
        return fields
    if currency:
        fields["salary_currency"] = str(currency)
    if period:
        fields["salary_period"] = str(period)
    return fields


def _workplace(job):
    pref = job.get("remotePreference")
    if isinstance(pref, dict):
        pref = pref.get("value") or pref.get("label")
    if pref:
        return str(pref)
    if job.get("remote"):
        return "remote"
    if job.get("hybrid"):
        return "hybrid"
    return None


def crawl(ctx):
    for seed in HOSTED_JOBS:
        detail_url = SITE.rstrip("/") + "/api-boards/jobs/%s?companyId=%s" % (
            quote(seed["job_id"], safe=""),
            quote(seed["company_id"], safe=""),
        )
        data = json.loads(ctx.fetch(detail_url))
        job = data.get("job") or {}

        title = _clean(job.get("title"))
        description = _clean(job.get("description"))
        if not title or not description:
            continue

        locations = _labels(job.get("locations"))
        job_types = _labels(job.get("jobTypes"))
        departments = _labels(job.get("departments"))
        job_id = str(job.get("id") or seed["job_id"])
        job_url = SITE.rstrip("/") + "/jobs/%s/%s" % (
            quote(seed["company_slug"], safe=""),
            quote(job_id, safe=""),
        )

        item = {
            "url": job_url,
            "title": title,
            "company": job.get("companyName") or seed["company_id"],
            "location": ", ".join(locations) if locations else "Remote",
            "description": description,
            "source": SITE,
            "external_id": job_id,
        }

        posted = job.get("postedDate") or job.get("posted") or job.get("created")
        if posted:
            item["posted_at"] = posted

        apply_url = job.get("url")
        if apply_url:
            item["apply_url"] = (
                SITE.rstrip("/") + apply_url
                if str(apply_url).startswith("/")
                else apply_url
            )

        workplace = _workplace(job)
        if workplace:
            item["workplace_type"] = workplace

        if job_types:
            item["employment_type"] = ", ".join(job_types)

        if departments:
            item["department"] = ", ".join(departments)

        for key, value in _salary_fields(job.get("salaryRange")).items():
            item[key] = value

        yield item