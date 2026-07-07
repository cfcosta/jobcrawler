import html
import re
from urllib.parse import urljoin

SITE = 'https://api.ashbyhq.com/posting-api/job-board/parity?includeCompensation=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _set_if_value(out, key, value):
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    out[key] = value


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None

    # Ashby summaryComponents carry the most structured salary data when present.
    pieces = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        label = component.get("label") or component.get("compensationType")
        if min_value is None and max_value is None:
            continue

        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value}-{max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)

        bits = [str(x) for x in (currency, amount, interval, label) if x]
        if bits:
            pieces.append(" ".join(bits))

    if pieces:
        return "; ".join(pieces)

    for key in ("scrapeableCompensationSalarySummary", "compensationTierSummary"):
        value = compensation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def crawl(ctx):
    data = ctx.fetch(SITE)
    if isinstance(data, str):
        import json
        data = json.loads(data)

    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        item = {
            "url": job.get("jobUrl") or SITE,
            "title": job.get("title"),
            "company": "Parity",
            "location": job.get("location"),
            "description": description,
            "source": SITE,
        }

        _set_if_value(item, "employment_type", job.get("employmentType"))
        _set_if_value(item, "workplace_type", job.get("workplaceType") or ("Remote" if job.get("isRemote") else None))
        _set_if_value(item, "department", job.get("department") or job.get("team"))
        _set_if_value(item, "posted_at", job.get("publishedAt"))
        _set_if_value(item, "external_id", job.get("id"))
        _set_if_value(item, "apply_url", job.get("applyUrl"))
        _set_if_value(item, "salary", _salary_from_compensation(job.get("compensation")))

        # Drop unset/empty required fields rather than yielding invalid blank strings.
        item = {k: v for k, v in item.items() if v is not None and (not isinstance(v, str) or v.strip())}
        if all(item.get(k) for k in ("url", "title", "company", "location", "description")):
            yield item
