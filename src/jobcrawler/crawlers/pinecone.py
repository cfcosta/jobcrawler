
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/pinecone?includeCompensation=true"
EXTRA_HOSTS = []

try:
    Job
except NameError:
    Job = dict

_COMPANY = "Pinecone"

def _clean_html(html):
    if not html:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _salary_text(compensation):
    if not isinstance(compensation, dict):
        return None

    summary = compensation.get("scrapeableCompensationSalarySummary")
    if summary:
        return summary

    for comp in compensation.get("summaryComponents") or []:
        if not isinstance(comp, dict):
            continue
        if comp.get("compensationType") == "Salary" and comp.get("summary"):
            return comp.get("summary")

    return compensation.get("compensationTierSummary") or None

def _add_if_present(out, key, value):
    if value not in (None, ""):
        out[key] = value

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        if not description:
            continue

        out = {
            "url": item.get("jobUrl") or SITE,
            "title": item.get("title"),
            "company": _COMPANY,
            "location": item.get("location"),
            "description": description,
            "source": SITE,
        }
        _add_if_present(out, "external_id", item.get("id"))
        _add_if_present(out, "apply_url", item.get("applyUrl"))
        _add_if_present(out, "department", item.get("department") or item.get("team"))
        _add_if_present(out, "employment_type", item.get("employmentType"))
        _add_if_present(out, "workplace_type", item.get("workplaceType"))
        _add_if_present(out, "posted_at", item.get("publishedAt"))
        _add_if_present(out, "salary", _salary_text(item.get("compensation")))

        yield Job(**out) if Job is not dict else out
