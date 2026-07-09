
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs?content=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _strip_html(value):
    if not value:
        return None
    s = html.unescape(value)
    out = []
    in_tag = False
    tag = ""
    for ch in s:
        if in_tag:
            if ch == ">":
                name = tag.strip().lower().split(" ", 1)[0].strip("/")
                if name in ("br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "table"):
                    out.append("\n")
                in_tag = False
                tag = ""
            else:
                tag += ch
        else:
            if ch == "<":
                in_tag = True
                tag = ""
            else:
                out.append(ch)
    text = html.unescape("".join(out))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return text.strip() or None


def _departments(raw):
    names = []
    for dept in raw.get("departments") or []:
        if isinstance(dept, dict):
            name = _clean(dept.get("name"))
        else:
            name = _clean(dept)
        if name:
            names.append(name)
    return "; ".join(names) if names else None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for raw in data.get("jobs", []):
        title = _clean(raw.get("title"))
        url = _clean(raw.get("absolute_url"))
        location = _clean((raw.get("location") or {}).get("name"))
        description = _strip_html(raw.get("content"))
        if not title or not url or not location or not description:
            continue

        external_id = _clean(raw.get("id"))
        if external_id is None:
            external_id = _clean(raw.get("internal_job_id"))
        if external_id is None:
            external_id = _clean(raw.get("requisition_id"))

        item = {
            "url": url,
            "title": title,
            "company": _clean(raw.get("company_name")) or "Scale AI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        department = _departments(raw)
        if department:
            item["department"] = department
        posted_at = _clean(raw.get("first_published"))
        if posted_at:
            item["posted_at"] = posted_at
        expires_at = _clean(raw.get("application_deadline"))
        if expires_at:
            item["expires_at"] = expires_at
        if external_id is not None:
            item["external_id"] = str(external_id)

        ranges = raw.get("pay_input_ranges") or []
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
            item["salary_min"] = min(r["min_cents"] for r in bands) / 100
            item["salary_max"] = max(r["max_cents"] for r in bands) / 100
            item["salary_currency"] = next(iter(currencies))

        yield item
