
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/langchain?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "LangChain"

def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, method="GET", payload="")

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _set_if_present(out, key, value):
    if value is not None:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return
        out[key] = value

def crawl(ctx):
    data = json.loads(_fetch(ctx, SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        url = job.get("jobUrl")
        title = job.get("title")
        location = job.get("location")
        if not (url and title and location and description):
            continue

        out = {
            "url": url.strip(),
            "title": title.strip(),
            "company": COMPANY,
            "location": location.strip(),
            "description": description.strip(),
            "source": SITE,
        }

        _set_if_present(out, "employment_type", job.get("employmentType"))
        _set_if_present(out, "workplace_type", job.get("workplaceType"))
        _set_if_present(out, "department", job.get("department") or job.get("team"))
        _set_if_present(out, "posted_at", job.get("publishedAt"))
        _set_if_present(out, "external_id", job.get("id"))
        _set_if_present(out, "apply_url", job.get("applyUrl"))

        # Ashby exposes structured compensation here when present. The current board
        # has no summary components, so salary fields are intentionally omitted.
        components = ((job.get("compensation") or {}).get("summaryComponents") or [])
        for component in components:
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")
            if min_value is not None or max_value is not None:
                _set_if_present(out, "salary_min", min_value)
                _set_if_present(out, "salary_max", max_value)
                _set_if_present(out, "salary_currency", currency)
                _set_if_present(out, "salary_period", interval)
                break

        yield out
