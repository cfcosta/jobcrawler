import html
import json
import re

# Workable widget API, account `lawnstarter`. `details=true` returns the full
# HTML description inline on every job, so the board comes back in one GET with
# no per-shortcode detail fetch. The widget expands one posting into one entry
# per location (same shortcode repeated ~6×), each entry carrying a single
# location — so group by shortcode and merge the locations back together.
SITE = "https://apply.workable.com/api/v1/widget/accounts/lawnstarter?details=true"
EXTRA_HOSTS = []

COMPANY = "LawnStarter"
ACCOUNT = "lawnstarter"


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _entry_locations(job):
    """Visible location labels carried by one widget entry."""
    labels = []
    for loc in job.get("locations") or []:
        if not isinstance(loc, dict) or loc.get("hidden"):
            continue
        parts = []
        for key in ("city", "region", "state", "country"):
            value = loc.get(key)
            if value and value not in parts:
                parts.append(str(value))
        if parts:
            label = ", ".join(parts)
            if label not in labels:
                labels.append(label)
    return labels


def _department(value):
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip()) or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def crawl(ctx):
    account = json.loads(ctx.fetch(SITE))

    groups = {}
    order = []
    for job in account.get("jobs") or []:
        shortcode = job.get("shortcode")
        if not shortcode:
            continue
        if shortcode not in groups:
            groups[shortcode] = {"job": job, "locations": []}
            order.append(shortcode)
        merged = groups[shortcode]["locations"]
        for label in _entry_locations(job):
            if label not in merged:
                merged.append(label)

    for shortcode in order:
        job = groups[shortcode]["job"]
        locations = list(groups[shortcode]["locations"])

        description = _clean_html(job.get("description"))
        if not description:
            continue

        if not locations:
            if job.get("telecommuting") is True or job.get("remote") is True:
                locations = ["Remote"]
            else:
                for key in ("city", "state", "country"):
                    value = job.get(key)
                    if value:
                        locations = [str(value)]
                        break
        if not locations:
            continue

        # Store account-scoped public URLs, NOT the widget's account-less
        # /j/<code> shortlinks — the §17 Workable identity key only fires on the
        # /<account>/j/<code> shape.
        out = {
            "url": f"https://apply.workable.com/{ACCOUNT}/j/{shortcode}/",
            "title": job.get("title"),
            "company": COMPANY,
            "location": locations[0],
            "description": description,
            "source": SITE,
            "external_id": str(shortcode),
            "apply_url": f"https://apply.workable.com/{ACCOUNT}/j/{shortcode}/apply/",
        }
        if len(locations) > 1:
            out["locations"] = locations

        employment_type = job.get("employment_type")
        if employment_type:
            out["employment_type"] = employment_type
        if job.get("telecommuting") is True or job.get("remote") is True:
            out["workplace_type"] = "remote"
        department = _department(job.get("department"))
        if department:
            out["department"] = department
        posted_at = job.get("published_on") or job.get("created_at")
        if posted_at:
            out["posted_at"] = posted_at

        yield out
