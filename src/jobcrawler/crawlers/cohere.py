
import html
import json
import re

SITE = 'https://api.ashbyhq.com/posting-api/job-board/cohere?includeCompensation=true'
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if min_value is None and max_value is None:
            continue
        if min_value is not None:
            out["salary_min"] = min_value
        if max_value is not None:
            out["salary_max"] = max_value
        if currency:
            out["salary_currency"] = currency
        if interval:
            out["salary_interval"] = interval
        return


def crawl(ctx):
    data = json.loads(_fetch_text(ctx, SITE))
    for job in data.get("jobs", []):
        description = _clean(job.get("descriptionPlain")) or _text_from_html(job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not title or not url or not location or not description:
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Cohere",
            "location": location,
            "description": description,
            "source": SITE,
        }

        field_map = {
            "id": "external_id",
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "applyUrl": "apply_url",
        }
        for source_key, dest_key in field_map.items():
            value = _clean(job.get(source_key))
            if value is not None:
                out[dest_key] = value

        _add_compensation(out, job)
        yield out
