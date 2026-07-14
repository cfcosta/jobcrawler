
import html
import json
import re


SITE = "https://api.ashbyhq.com/posting-api/job-board/flashbots.net?includeCompensation=true"
EXTRA_HOSTS = []
COMPANY = "Flashbots"


def _plain_description(source):
    text = source.get("descriptionPlain")
    if text:
        return text.strip()

    markup = source.get("descriptionHtml")
    if not markup:
        return None
    markup = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    markup = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", markup)
    text = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip() or None


def _locations(source):
    result = []
    primary = source.get("location")
    if primary:
        result.append(primary)

    for item in source.get("secondaryLocations") or []:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("location")
        else:
            value = None
        if value and value not in result:
            result.append(value)
    return result


def _salary(source):
    compensation = source.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    candidates = []
    for component in components:
        if not isinstance(component, dict):
            continue
        kind = component.get("compensationType") or component.get("type")
        if kind and str(kind).lower() != "salary":
            continue
        if component.get("minValue") is not None or component.get("maxValue") is not None:
            candidates.append(component)

    if not candidates:
        return {}

    currencies = {
        component.get("currencyCode")
        for component in candidates
        if component.get("currencyCode")
    }
    if len(currencies) > 1:
        return {}

    component = candidates[0]
    values = {}
    mappings = {
        "minValue": "salary_min",
        "maxValue": "salary_max",
        "currencyCode": "salary_currency",
        "interval": "salary_period",
    }
    for source_key, target_key in mappings.items():
        value = component.get(source_key)
        if value is not None and value != "":
            values[target_key] = value

    printed = compensation.get("compensationTierSummary") or compensation.get("summary")
    if printed:
        values["salary"] = printed
    return values


def _put(values, key, value):
    if value is not None and value != "" and value != []:
        values[key] = value


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for source in payload.get("jobs") or []:
        values = {"company": COMPANY}
        _put(values, "url", source.get("jobUrl"))
        _put(values, "title", source.get("title"))
        _put(values, "location", source.get("location"))
        _put(values, "description", _plain_description(source))
        _put(values, "employment_type", source.get("employmentType"))
        _put(values, "workplace_type", source.get("workplaceType"))
        _put(values, "department", source.get("department"))
        _put(values, "posted_at", source.get("publishedAt"))
        _put(values, "expires_at", source.get("expiresAt"))
        _put(values, "external_id", source.get("id"))
        _put(values, "apply_url", source.get("applyUrl"))

        locations = _locations(source)
        if locations:
            values["locations"] = locations

        if not values.get("workplace_type") and source.get("isRemote") is True:
            values["workplace_type"] = "Remote"

        values.update(_salary(source))
        yield Job(**values)
