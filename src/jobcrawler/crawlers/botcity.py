
import json
import re
from html import unescape
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://api.quickin.io/public/67f6b4c5dcf76400135d3538/jobs"
EXTRA_HOSTS = []


def _job(**kwargs):
    return {key: value for key, value in kwargs.items() if value is not None and value != ""}


def _html_to_text(value):
    if not value:
        return None

    text = unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|li|div|section|h[1-6]|tr|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    if isinstance(response, (bytes, bytearray)):
        return response.decode("utf-8", "replace")
    return response


def _with_page(url, page):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    query.setdefault("limit", "30")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _location(doc):
    bits = [doc.get("city"), doc.get("region"), doc.get("country")]
    return ", ".join(str(bit).strip() for bit in bits if bit and str(bit).strip())


def crawl(ctx):
    page = 1
    pages = None
    seen = set()

    while pages is None or page <= pages:
        url = SITE if page == 1 else _with_page(SITE, page)
        data = json.loads(_fetch_text(ctx, url))
        pages = int(data.get("pages") or 1)

        docs = data.get("docs") or []
        if not docs:
            break

        for doc in docs:
            if doc.get("publicate") and doc.get("publicate") != "published":
                continue

            external_id = doc.get("_id")
            if external_id in seen:
                continue
            seen.add(external_id)

            description_parts = [
                _html_to_text(doc.get("description")),
                _html_to_text(doc.get("requirements")),
            ]
            description = "\n\n".join(part for part in description_parts if part)
            location = _location(doc)

            yield _job(
                url=doc.get("career_url") or SITE,
                title=doc.get("title"),
                company="BotCity",
                location=location,
                description=description,
                source=SITE,
                department=doc.get("position_category"),
                workplace_type=doc.get("workplace_type"),
                posted_at=doc.get("created_at"),
                external_id=external_id,
                apply_url=doc.get("career_url"),
            )

        page += 1
