
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/io-global"
EXTRA_HOSTS = []

COMPANY = "IO Global"
ACCOUNT = "io-global"


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location_text(job):
    locations = job.get("locations") or []
    parts = []
    for loc in locations:
        if loc.get("hidden"):
            continue
        bits = [loc.get("city"), loc.get("region"), loc.get("country")]
        label = ", ".join(str(x).strip() for x in bits if x)
        if label and label not in parts:
            parts.append(label)

    if not parts:
        loc = job.get("location") or {}
        bits = [loc.get("city"), loc.get("region"), loc.get("country")]
        label = ", ".join(str(x).strip() for x in bits if x)
        if label:
            parts.append(label)

    if parts:
        return "; ".join(parts)

    if job.get("remote") or job.get("telecommuting"):
        return "Remote"

    return None


def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _employment_type(job):
    value = _first_nonempty(job.get("employment_type"), job.get("type"))
    if value == "full":
        return "Full-time"
    if value == "part":
        return "Part-time"
    if isinstance(value, str):
        return value.strip() or None
    return value


def _department(job):
    value = job.get("department")
    if isinstance(value, list):
        joined = ", ".join(str(x).strip() for x in value if x)
        return joined or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _workplace_type(job):
    workplace = job.get("workplace")
    if workplace:
        return str(workplace).replace("_", " ")
    if job.get("remote") or job.get("telecommuting"):
        return "remote"
    return None


def _detail_url(shortcode):
    return "https://apply.workable.com/api/v1/accounts/%s/jobs/%s" % (ACCOUNT, shortcode)


def _posting_url(job):
    return _first_nonempty(
        job.get("url"),
        job.get("shortlink"),
        "https://apply.workable.com/j/%s" % job.get("shortcode"),
    )


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    seen = set()

    for listed in board.get("jobs") or []:
        shortcode = listed.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        detail = json.loads(ctx.fetch(_detail_url(shortcode)))
        state = detail.get("state")
        if state and state != "published":
            continue

        # Workable stores the full posting body across these HTML fields.
        sections = [
            _clean_html(detail.get("description")),
            _clean_html(detail.get("requirements")),
            _clean_html(detail.get("benefits")),
        ]
        description = "\n\n".join(section for section in sections if section)
        if not description:
            continue

        title = _first_nonempty(detail.get("title"), listed.get("title"))
        location = _location_text(detail) or _location_text(listed)
        if not title or not location:
            continue

        job = {
            "url": _posting_url(listed),
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(_first_nonempty(detail.get("id"), detail.get("shortcode"), shortcode)),
            "apply_url": _first_nonempty(listed.get("application_url"), listed.get("url")),
            "employment_type": _employment_type(detail) or _employment_type(listed),
            "workplace_type": _workplace_type(detail) or _workplace_type(listed),
            "department": _department(detail) or _department(listed),
            "posted_at": _first_nonempty(detail.get("published"), listed.get("published_on"), listed.get("created_at")),
        }

        yield {k: v for k, v in job.items() if v is not None and v != ""}
