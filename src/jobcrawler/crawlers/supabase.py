
import html
import json
import re

SITE = 'https://api.ashbyhq.com/posting-api/job-board/supabase?includeCompensation=true'
EXTRA_HOSTS = []

COMPANY = "Supabase"


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if isinstance(response, str):
        return response
    if hasattr(response, "text"):
        return response.text
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, bytes):
            return content.decode("utf-8", "replace")
        return str(content)
    return str(response)


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _set_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None
    components = compensation.get("summaryComponents") or []
    parts = []
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value}-{max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)
        if currency:
            amount = f"{currency} {amount}"
        if interval:
            amount = f"{amount} {interval}"
        parts.append(amount)
    return "; ".join(parts) if parts else None


def crawl(ctx):
    data = json.loads(_fetch_text(ctx, SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        if not description:
            continue

        job = {
            "url": item.get("jobUrl") or (SITE.rstrip("/") + "/" + item.get("id", "")),
            "title": item.get("title"),
            "company": COMPANY,
            "location": item.get("location") or "Remote",
            "description": description,
            "source": SITE,
        }

        _set_if_present(job, "external_id", item.get("id"))
        _set_if_present(job, "department", item.get("department") or item.get("team"))
        _set_if_present(job, "employment_type", item.get("employmentType"))
        _set_if_present(job, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") else None))
        _set_if_present(job, "posted_at", item.get("publishedAt"))
        _set_if_present(job, "apply_url", item.get("applyUrl"))
        _set_if_present(job, "salary", _salary_from_compensation(item.get("compensation")))

        if job.get("url") and job.get("title") and job.get("location"):
            yield job
