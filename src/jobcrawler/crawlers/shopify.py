
import re
import html

SITE = "https://www.shopify.com/careers.data"
EXTRA_HOSTS = []

FEED_URL = "https://www.shopify.com/careers/feed.xml"
COMPANY = "Shopify"


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if isinstance(response, str):
        return response
    if hasattr(response, "text"):
        text = response.text
        return text() if callable(text) else text
    return str(response)


def _field(block, name):
    pattern = r"<%s\b[^>]*>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</%s>" % (
        re.escape(name),
        re.escape(name),
    )
    match = re.search(pattern, block, flags=re.S | re.I)
    if not match:
        return None
    value = match.group(1) if match.group(1) is not None else match.group(2)
    value = html.unescape(value.strip())
    return value or None


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _clean_url(value):
    if not value:
        return None
    url = value.strip()
    # The feed's applyUrl is the canonical posting path plus a tracking query.
    return url.split("?", 1)[0]


def _set_if_present(out, key, value):
    if value is not None and value != "":
        out[key] = value


def crawl(ctx):
    text = _fetch_text(ctx, FEED_URL)
    blocks = re.findall(r"<job\b[^>]*>(.*?)</job>", text, flags=re.S | re.I)

    for block in blocks:
        external_id = _field(block, "partnerJobId")
        title = _field(block, "title")
        description = _strip_html(_field(block, "description"))
        apply_url = _field(block, "applyUrl")
        url = _clean_url(_field(block, "url") or apply_url)
        location = _field(block, "location")

        if not title or not description or not url or not location:
            continue

        job = {
            "url": url,
            "title": title,
            "company": _field(block, "company") or COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        _set_if_present(job, "external_id", external_id)
        _set_if_present(job, "apply_url", apply_url)
        _set_if_present(job, "department", _field(block, "department") or _field(block, "team"))
        _set_if_present(job, "employment_type", _field(block, "employmentType"))
        _set_if_present(job, "workplace_type", _field(block, "workplaceType"))
        _set_if_present(job, "posted_at", _field(block, "datePosted"))
        _set_if_present(job, "expires_at", _field(block, "validThrough"))

        yield job
