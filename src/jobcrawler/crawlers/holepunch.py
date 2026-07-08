
import html
import json
import re

SITE = "https://holepunch.recruitee.com/api/offers/"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_RECRUITEE_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC$")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _field(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _string(value):
    value = _field(value)
    if value is None:
        return None
    return str(value)

def _datetime(value):
    value = _field(value)
    if not value:
        return None
    match = _RECRUITEE_DATE_RE.match(value)
    if match:
        return match.group(1) + "T" + match.group(2) + "Z"
    return value

def _employment_type(code):
    mapping = {
        "fulltime_permanent": "Full-time permanent",
        "fulltime": "Full-time",
        "parttime": "Part-time",
        "contract": "Contract",
        "temporary": "Temporary",
        "internship": "Internship",
    }
    return mapping.get(code, code.replace("_", " ").title() if isinstance(code, str) and code else None)

def _workplace_type(offer):
    if offer.get("remote"):
        return "Remote"
    if offer.get("hybrid"):
        return "Hybrid"
    if offer.get("on_site"):
        return "On-site"
    return None

def _salary(offer):
    salary = offer.get("salary")
    if not isinstance(salary, dict):
        return None
    parts = []
    currency = _field(salary.get("currency"))
    min_value = salary.get("min")
    max_value = salary.get("max")
    period = _field(salary.get("period"))
    if min_value is not None and max_value is not None:
        parts.append("%s-%s" % (min_value, max_value))
    elif min_value is not None:
        parts.append("From %s" % min_value)
    elif max_value is not None:
        parts.append("Up to %s" % max_value)
    if currency:
        parts.append(currency)
    if period:
        parts.append(period)
    return " ".join(str(p) for p in parts).strip() or None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for offer in data.get("offers") or []:
        if offer.get("status") and offer.get("status") != "published":
            continue

        description_parts = []
        for key in ("description", "requirements"):
            text = _clean_html(offer.get(key))
            if text:
                description_parts.append(text)
        description = "\n\n".join(description_parts).strip()
        if not description:
            continue

        job = {
            "url": _field(offer.get("careers_url")) or SITE,
            "title": _field(offer.get("title")),
            "company": "Holepunch",
            "location": _field(offer.get("location")) or _field(offer.get("city")) or "Remote",
            "description": description,
            "source": SITE,
        }

        optional = {
            "external_id": _string(offer.get("id")),
            "apply_url": _field(offer.get("careers_apply_url")),
            "department": _field(offer.get("department")),
            "employment_type": _employment_type(offer.get("employment_type_code")),
            "workplace_type": _workplace_type(offer),
            "posted_at": _datetime(offer.get("published_at")),
            "expires_at": _datetime(offer.get("close_at")),
            "salary": _salary(offer),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                job[key] = value

        yield job
