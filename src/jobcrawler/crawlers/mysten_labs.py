
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/mystenlabs?includeCompensation=true"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section)\s*>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

def _present(value):
    return value is not None and value != ""

def _compensation(comp):
    if not isinstance(comp, dict):
        return None
    parts = []
    summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if summary:
        parts.append(str(summary))
    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = f"{min_value}-{max_value}"
        else:
            amount = str(min_value if min_value is not None else max_value)
        detail = " ".join(str(x) for x in [
            component.get("currencyCode"),
            amount,
            component.get("interval"),
        ] if x)
        label = component.get("label") or component.get("compensationType")
        if label:
            detail = f"{label}: {detail}"
        parts.append(detail)
    return "; ".join(parts) if parts else None

def _job(**fields):
    return {k: v for k, v in fields.items() if _present(v)}

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        if not description:
            continue
        yield _job(
            source=SITE,
            url=item.get("jobUrl"),
            title=item.get("title"),
            company="Mysten Labs",
            location=item.get("location"),
            description=description,
            employment_type=item.get("employmentType"),
            workplace_type=item.get("workplaceType"),
            department=item.get("department") or item.get("team"),
            posted_at=item.get("publishedAt"),
            external_id=item.get("id"),
            apply_url=item.get("applyUrl"),
            salary=_compensation(item.get("compensation")),
        )
