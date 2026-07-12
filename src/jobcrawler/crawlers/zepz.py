
import html
import json
import re


SITE = "https://zepz.careers.hibob.com/api/job-ad"
EXTRA_HOSTS = []

_BOARD_ROOT = "https://zepz.careers.hibob.com"
_BLOCK_TAGS = (
    "address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|"
    "h1|h2|h3|h4|h5|h6|header|hr|li|main|ol|p|pre|section|table|tbody|td|"
    "tfoot|th|thead|tr|ul"
)


def _plain_text(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(
        r"</?(?:" + _BLOCK_TAGS + r")\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first(mapping, *names):
    for name in names:
        value = mapping.get(name)
        if value is not None and value != "":
            return value
    return None


def _description(item):
    sections = []
    for field in ("description", "responsibilities", "requirements", "benefits"):
        text = _plain_text(item.get(field))
        if text:
            sections.append(text)
    return "\n\n".join(sections) or None


def crawl(ctx):
    response = ctx.fetch(
        SITE,
        headers={
            "Referer": _BOARD_ROOT + "/",
            "Accept": "application/json",
        },
    )
    payload = json.loads(response)

    for item in payload.get("jobAdDetails", []):
        external_id = item.get("id")
        title = item.get("title")
        description = _description(item)
        location = item.get("site") or item.get("country")

        if not external_id or not title or not description or not location:
            continue

        job_url = _BOARD_ROOT + "/jobs/" + str(external_id)
        pay = item.get("payTransparency") or {}
        kwargs = {
            "url": job_url,
            "source": SITE,
            "apply_url": job_url,
            "title": title,
            "company": "Zepz",
            "location": location,
            "description": description,
            "external_id": str(external_id),
        }

        optional = {
            "department": item.get("department"),
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workspaceType"),
            "posted_at": item.get("publishedAt"),
            "salary_min": _first(
                pay, "MinSalary", "minSalary", "minimumSalary", "min"
            ),
            "salary_max": _first(
                pay, "MaxSalary", "maxSalary", "maximumSalary", "max"
            ),
            "salary_currency": _first(
                pay, "Currency", "currency", "currencyCode"
            ),
            "salary_period": _first(
                pay, "PayPeriod", "payPeriod", "period"
            ),
        }
        kwargs.update({
            key: value
            for key, value in optional.items()
            if value is not None and value != ""
        })

        yield kwargs
