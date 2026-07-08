
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/monad.foundation?includeCompensation=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>", "\n", str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _clean_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _put(job, key, value):
    if value is not None and value != "":
        job[key] = value


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None
    pieces = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = "%s-%s" % (min_value, max_value)
        else:
            amount = str(min_value if min_value is not None else max_value)
        if currency:
            amount = currency + " " + amount
        if interval:
            amount = amount + " " + str(interval)
        pieces.append(amount)
    return "; ".join(pieces) if pieces else None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        title = _clean_text(item.get("title"))
        url = item.get("jobUrl")
        location = _clean_text(item.get("location"))
        description = _clean_text(item.get("descriptionPlain")) or _html_to_text(item.get("descriptionHtml"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Monad Foundation",
            "location": location,
            "description": description,
            "source": SITE,
        }
        _put(job, "external_id", item.get("id"))
        _put(job, "department", _clean_text(item.get("department") or item.get("team")))
        _put(job, "employment_type", _clean_text(item.get("employmentType")))
        _put(job, "workplace_type", _clean_text(item.get("workplaceType")))
        _put(job, "posted_at", item.get("publishedAt"))
        _put(job, "apply_url", item.get("applyUrl"))
        _put(job, "salary", _salary_from_compensation(item.get("compensation")))
        yield job
