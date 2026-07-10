
import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://api.quickin.io/public/6409d3f1988fca0014f2d9c1/jobs"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _url_with_page(page):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = _WS_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _description(doc):
    parts = []
    for label, field in (("", "description"), ("Requisitos", "requirements"), ("Beneficios", "benefits")):
        cleaned = _clean_html(doc.get(field))
        if cleaned:
            parts.append((label + ":\n" + cleaned) if label else cleaned)
    return "\n\n".join(parts) or None


def _compact(job):
    return {key: value for key, value in job.items() if value not in (None, "", [])}


def _iter_docs(ctx):
    first = json.loads(ctx.fetch(SITE))
    for doc in first.get("docs") or []:
        yield doc
    pages = int(first.get("pages") or 1)
    for page in range(2, pages + 1):
        data = json.loads(ctx.fetch(_url_with_page(page)))
        for doc in data.get("docs") or []:
            yield doc


def crawl(ctx):
    seen = set()
    for doc in _iter_docs(ctx):
        if doc.get("publicate") != "published":
            continue
        external_id = doc.get("_id") or doc.get("code")
        if external_id in seen:
            continue
        seen.add(external_id)

        title = doc.get("title")
        description = _description(doc)
        if not title or not description:
            continue

        city = doc.get("city")
        region = doc.get("region")
        country = doc.get("country")
        location = ", ".join(part for part in (city, region, country) if part)

        contract = doc.get("contract_id") or {}

        yield _compact({
            "url": doc.get("career_url") or (SITE.rstrip("/") + "/" + str(external_id)),
            "title": title,
            "company": "Grupo Carone",
            "location": location,
            "description": description,
            "source": SITE,
            "employment_type": contract.get("name") if isinstance(contract, dict) else None,
            "workplace_type": doc.get("workplace_type"),
            "department": doc.get("position_category"),
            "posted_at": doc.get("created_at"),
            "external_id": external_id,
            "apply_url": doc.get("career_url"),
        })
