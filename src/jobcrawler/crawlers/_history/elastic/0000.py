
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def _clean_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NL_RE.sub("\n\n", text)
    text = text.strip()
    return text or None


def _set_if_present(job, key, value):
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    job[key] = value


def _department_name(raw):
    if isinstance(raw, list) and raw:
        names = []
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]).strip())
        return ", ".join(n for n in names if n) or None
    if isinstance(raw, dict):
        return raw.get("name")
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs", []):
        loc = item.get("location") or {}
        url = item.get("absolute_url") or item.get("url")
        description = _clean_text(item.get("content"))
        out = {
            "url": url,
            "title": item.get("title"),
            "company": "Elastic",
            "location": loc.get("name") if isinstance(loc, dict) else loc,
            "description": description,
            "source": SITE,
        }

        _set_if_present(out, "external_id", str(item.get("id") or item.get("internal_job_id") or ""))
        _set_if_present(out, "department", _department_name(item.get("departments") or item.get("department")))

        apply_url = item.get("absolute_url")
        if apply_url and apply_url != url:
            _set_if_present(out, "apply_url", apply_url)

        ranges = item.get("pay_input_ranges") or []
        bands = [
            r for r in ranges
            if isinstance(r, dict) and r.get("min_cents") and r.get("max_cents")
        ]
        currencies = {
            str(r.get("currency_type") or "").strip().upper() for r in bands
        }
        # Single-currency boards only: mixed-currency ranges (one per region)
        # cannot collapse into one honest band, so leave those unstated.
        if bands and len(currencies) == 1 and "" not in currencies:
            out["salary_min"] = min(r["min_cents"] for r in bands) / 100
            out["salary_max"] = max(r["max_cents"] for r in bands) / 100
            out["salary_currency"] = next(iter(currencies))

        # Drop any source-silent or blank values so schema validation does not see empty strings.
        out = {k: v for k, v in out.items() if v is not None and v != ""}
        yield out
