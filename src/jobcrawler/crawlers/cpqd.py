
import html
import json
import re

SITE = "https://cpqd.recruitee.com/api/offers/"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _normalize_date(value):
    if not value:
        return None
    value = str(value).strip()
    if value.endswith(" UTC"):
        return value[:-4].replace(" ", "T", 1) + "+00:00"
    return value


def _workplace_type(offer):
    if offer.get("remote") is True:
        return "remote"
    if offer.get("hybrid") is True:
        return "hybrid"
    if offer.get("on_site") is True:
        return "on_site"
    return None


def _add_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value


def _add_salary(job, salary):
    if isinstance(salary, str):
        _add_if_present(job, "salary", salary)
        return
    if not isinstance(salary, dict):
        return

    _add_if_present(job, "salary_min", salary.get("min"))
    _add_if_present(job, "salary_max", salary.get("max"))
    _add_if_present(job, "salary_currency", salary.get("currency"))
    _add_if_present(job, "salary_period", salary.get("period"))


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for offer in data.get("offers", []):
        status = offer.get("status")
        if status and str(status).lower() not in {"published", "open", "active"}:
            continue

        parts = []
        for field in ("description", "requirements"):
            text = _text_from_html(offer.get(field))
            if text:
                parts.append(text)
        description = "\n\n".join(parts).strip()
        if not description:
            continue

        url = offer.get("careers_url") or (SITE.rstrip("/") + "/" + str(offer.get("slug", "")))
        loc = offer.get("location")
        if not loc:
            names = [l.get("name") for l in offer.get("locations", []) if isinstance(l, dict) and l.get("name")]
            loc = ", ".join(names) if names else None

        job = {
            "url": url,
            "title": offer.get("title"),
            "company": "CPQD",
            "location": loc,
            "description": description,
            "source": SITE,
        }

        locations = [l.get("name") for l in offer.get("locations", []) if isinstance(l, dict) and l.get("name")]
        if locations:
            job["locations"] = locations

        _add_if_present(job, "department", offer.get("department"))
        _add_if_present(job, "employment_type", offer.get("employment_type_code"))
        _add_if_present(job, "workplace_type", _workplace_type(offer))
        _add_if_present(job, "posted_at", _normalize_date(offer.get("published_at") or offer.get("created_at")))
        _add_if_present(job, "expires_at", _normalize_date(offer.get("close_at")))
        _add_if_present(job, "external_id", str(offer.get("id")) if offer.get("id") is not None else None)
        _add_if_present(job, "apply_url", offer.get("careers_apply_url"))
        _add_salary(job, offer.get("salary"))

        yield job
