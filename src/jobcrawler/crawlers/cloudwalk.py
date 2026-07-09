
import json
import re
import html
from datetime import datetime, timezone

SITE = "https://api.lever.co/v0/postings/cloudwalk?mode=json"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _parts_to_description(posting):
    parts = []
    for key in ("description",):
        cleaned = _clean_html(posting.get(key))
        if cleaned:
            parts.append(cleaned)

    for section in posting.get("lists") or []:
        heading = _clean_html(section.get("text") or section.get("title"))
        body = _clean_html(section.get("content"))
        if heading and body:
            parts.append(heading + "\n" + body)
        elif body:
            parts.append(body)
        elif heading:
            parts.append(heading)

    additional = _clean_html(posting.get("additional"))
    if additional:
        parts.append(additional)

    return "\n\n".join(part for part in parts if part).strip()


def _posted_at(created_at):
    if created_at is None:
        return None
    try:
        return datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def _set_if_value(obj, key, value):
    if value is not None and value != "":
        obj[key] = value


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


def _amount_text(number):
    if number.is_integer():
        return str(int(number))
    return ("%f" % number).rstrip("0").rstrip(".")


def _salary_fields(salary_range):
    """Map Lever's salaryRange into the salary contract fields.

    Lever serves salaryRange as an object {min, max, currency, interval}; assigning
    that dict into the str-typed `salary` field fails contract validation at the
    runner and drops the whole posting. Strings stay verbatim; objects become the
    structured quartet plus a readable text. Zero/missing bounds are omitted.
    """
    if isinstance(salary_range, str):
        text = salary_range.strip()
        return {"salary": text} if text else {}
    if not isinstance(salary_range, dict):
        return {}

    low = _positive_amount(salary_range.get("min"))
    high = _positive_amount(salary_range.get("max"))
    if low is None and high is None:
        return {}

    currency = salary_range.get("currency")
    currency = str(currency).strip() if currency else None
    interval = salary_range.get("interval")
    interval = str(interval).strip() if interval else None

    fields = {}
    if low is not None:
        fields["salary_min"] = low
    if high is not None:
        fields["salary_max"] = high
    if currency:
        fields["salary_currency"] = currency
    if interval:
        fields["salary_period"] = interval

    if low is not None and high is not None and low != high:
        amount = _amount_text(low) + " - " + _amount_text(high)
    else:
        amount = _amount_text(low if low is not None else high)
    text = " ".join(bit for bit in (currency, amount) if bit)
    if interval:
        text = text + " / " + interval
    fields["salary"] = text
    return fields


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    for posting in postings:
        categories = posting.get("categories") or {}

        job = {
            "url": posting.get("hostedUrl") or posting.get("applyUrl"),
            "title": posting.get("text"),
            "company": "CloudWalk",
            "location": categories.get("location"),
            "description": _parts_to_description(posting),
            "source": SITE,
        }

        _set_if_value(job, "external_id", posting.get("id"))
        _set_if_value(job, "apply_url", posting.get("applyUrl"))
        _set_if_value(job, "workplace_type", posting.get("workplaceType"))
        _set_if_value(job, "department", categories.get("team") or categories.get("department"))
        _set_if_value(job, "employment_type", categories.get("commitment"))
        _set_if_value(job, "posted_at", _posted_at(posting.get("createdAt")))

        for key, value in _salary_fields(posting.get("salaryRange")).items():
            _set_if_value(job, key, value)

        yield job
