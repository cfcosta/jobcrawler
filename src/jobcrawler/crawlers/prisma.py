
SITE = 'https://api.rippling.com/platform/api/ats/v1/board/prisma-careers/jobs'
EXTRA_HOSTS = []

def _clean_html(value):
    import html
    import re

    if not value:
        return None

    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _first_text(*values):
    for value in values:
        if isinstance(value, dict):
            value = value.get("label") or value.get("name") or value.get("id")
        if value:
            return str(value)
    return None

def _money_value(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None

def _apply_salary(job, pay_ranges):
    if not isinstance(pay_ranges, list) or not pay_ranges:
        return

    currencies = set()
    mins = []
    maxs = []
    periods = []
    printed = []

    for item in pay_ranges:
        if not isinstance(item, dict):
            continue

        currency = item.get("currency") or item.get("currencyCode") or item.get("currency_type") or item.get("currencyType")
        if isinstance(currency, dict):
            currency = currency.get("code") or currency.get("id") or currency.get("label")
        if currency:
            currencies.add(str(currency))

        for key in ("min", "minimum", "minValue", "minAmount", "min_cents"):
            if key in item:
                val = _money_value(item.get(key))
                if val is not None:
                    if key.endswith("_cents"):
                        val = val / 100.0
                    mins.append(val)
                    break

        for key in ("max", "maximum", "maxValue", "maxAmount", "max_cents"):
            if key in item:
                val = _money_value(item.get(key))
                if val is not None:
                    if key.endswith("_cents"):
                        val = val / 100.0
                    maxs.append(val)
                    break

        period = item.get("period") or item.get("interval") or item.get("payPeriod") or item.get("rate")
        if isinstance(period, dict):
            period = period.get("label") or period.get("id") or period.get("name")
        if period:
            periods.append(str(period))

        text = item.get("label") or item.get("display") or item.get("range") or item.get("description")
        if text:
            printed.append(str(text))

    if len(currencies) == 1:
        job["salary_currency"] = next(iter(currencies))
        if mins:
            job["salary_min"] = min(mins)
        if maxs:
            job["salary_max"] = max(maxs)
    if periods:
        job["salary_period"] = periods[0]
    if printed:
        job["salary"] = " / ".join(printed)

def crawl(ctx):
    import json

    raw = ctx.fetch(SITE)
    listings = json.loads(raw)
    if not isinstance(listings, list):
        return

    base = SITE.rstrip("/")
    for listing in listings:
        if not isinstance(listing, dict):
            continue

        uuid = listing.get("uuid")
        detail = listing
        if uuid:
            try:
                detail_raw = ctx.fetch(base + "/" + str(uuid))
                loaded = json.loads(detail_raw)
                if isinstance(loaded, dict):
                    detail = loaded
            except Exception:
                detail = listing

        title = detail.get("name") or listing.get("name")
        url = detail.get("url") or listing.get("url")
        location = _first_text(detail.get("workLocation"), listing.get("workLocation"))

        work_locations = []
        for loc in detail.get("workLocations") or []:
            loc_text = _first_text(loc)
            if loc_text and loc_text not in work_locations:
                work_locations.append(loc_text)
        if location and location not in work_locations:
            work_locations.insert(0, location)

        desc_obj = detail.get("description") if isinstance(detail.get("description"), dict) else {}
        pieces = []
        for key in ("company", "role"):
            cleaned = _clean_html(desc_obj.get(key))
            if cleaned:
                pieces.append(cleaned)
        description = "\n\n".join(pieces).strip()

        if not (title and url and location and description):
            continue

        department = _first_text(detail.get("department"), listing.get("department"))
        employment_type = _first_text(detail.get("employmentType"))
        company = detail.get("companyName") or "Prisma"

        job = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
        }

        if work_locations:
            job["locations"] = work_locations
        if department:
            job["department"] = department
        if employment_type:
            job["employment_type"] = employment_type
        if detail.get("createdOn"):
            job["posted_at"] = detail.get("createdOn")
        if uuid:
            job["external_id"] = str(uuid)

        _apply_salary(job, detail.get("payRangeDetails"))

        yield job
