
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/truelogic?includeCompensation=true'
EXTRA_HOSTS = []

def _response_text(resp):
    if hasattr(resp, "text"):
        return resp.text
    if isinstance(resp, bytes):
        return resp.decode("utf-8", "replace")
    return str(resp)

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"<br\\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|section|tr)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None

def _add_if_present(job, key, value):
    if value is not None and value != "":
        job[key] = value

def _apply_compensation(out, comp):
    if not isinstance(comp, dict):
        return

    summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    _add_if_present(out, "salary", summary)

    components = comp.get("summaryComponents") or []
    if not components:
        return

    # Ashby summaryComponents may include a structured salary/rate component.
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None and not currency:
            continue
        _add_if_present(out, "salary_min", min_value)
        _add_if_present(out, "salary_max", max_value)
        _add_if_present(out, "salary_currency", currency)
        _add_if_present(out, "salary_period", interval)
        break

def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        title = item.get("title")
        location = item.get("location")
        url = item.get("jobUrl") or (SITE.split("?")[0].rstrip("/") + "/" + item.get("id", ""))

        if not (title and location and description and url):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Truelogic",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = []
        if location:
            locations.append(location)
        for secondary in item.get("secondaryLocations") or []:
            if isinstance(secondary, dict) and secondary.get("location"):
                locations.append(secondary["location"])
            elif isinstance(secondary, str) and secondary:
                locations.append(secondary)
        if len(locations) > 1:
            out["locations"] = locations

        _add_if_present(out, "external_id", item.get("id"))
        _add_if_present(out, "apply_url", item.get("applyUrl"))
        _add_if_present(out, "employment_type", item.get("employmentType"))
        _add_if_present(out, "workplace_type", item.get("workplaceType") or ("Remote" if item.get("isRemote") is True else None))
        _add_if_present(out, "department", item.get("department") or item.get("team"))
        _add_if_present(out, "posted_at", item.get("publishedAt"))

        _apply_compensation(out, item.get("compensation"))

        yield out
