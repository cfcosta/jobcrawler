
import re
import html
import json
from urllib.parse import urljoin

SITE = "https://jobs.zama.org/"
EXTRA_HOSTS = []

COMPANY = "Zama"


def _clean_text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _strip_tags(markup):
    if not markup:
        return None
    text = markup
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|section|article|h[1-6])\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)


def _attr(tag, name):
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*(['\"])(.*?)\1", tag or "", re.I | re.S)
    return html.unescape(m.group(2)) if m else None


def _last(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text or "", flags)
    return m.group(m.lastindex).strip() if m and m.lastindex else None


def _extract_index_jobs(index_html):
    jobs = []
    for match in re.finditer(r"<a\b([^>]*\bclass=(['\"])[^'\"]*jobs-list-item-link[^'\"]*\2[^>]*)>(.*?)</a>", index_html, re.I | re.S):
        attrs, _, inner = match.groups()
        href = _attr(attrs, "href")
        if not href or "/jobs/" not in href or "candidatures-spontanees" in href:
            continue
        title = _strip_tags(_last(r"<h3\b[^>]*class=(['\"])[^'\"]*jobs-list-item-title[^'\"]*\1[^>]*>(.*?)</h3>", inner) or "")
        contract = _strip_tags(_last(r"<li\b[^>]*class=(['\"])[^'\"]*jobs-list-item-contract-type[^'\"]*\1[^>]*>(.*?)</li>", inner) or "")
        department = _strip_tags(_last(r"<li\b[^>]*class=(['\"])[^'\"]*jobs-list-item-department[^'\"]*\1[^>]*>(.*?)</li>", inner) or "")
        jobs.append({
            "url": urljoin(SITE, href),
            "title": title,
            "employment_type": contract,
            "department": department,
        })
    return jobs


def _json_string_field(text, key):
    # The page's JSON-LD contains literal newlines in strings, so parse selected fields leniently.
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:\\.|[^"\\])*)"', text or "", re.S)
    if not m:
        return None
    raw = m.group(1).replace("\n", "\\n")
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return html.unescape(m.group(1)).replace('\\"', '"').replace("\\n", "\n").strip()


def _extract_location(page_html):
    loc = _json_string_field(page_html, "addressLocality")
    if loc:
        return _clean_text(loc)
    m = re.search(r"<i\b[^>]*class=(['\"])[^'\"]*icon-location[^'\"]*\1[^>]*></i>([\s\S]{0,300})", page_html, re.I)
    if m:
        text = _strip_tags(m.group(2))
        if text:
            return text.split("\n")[0].strip()
    return None


def _extract_description(page_html):
    # Keep the real posting body and exclude the company About block, gallery, apply widget, and related roles.
    m = re.search(
        r"<div\b[^>]*class=(['\"])[^'\"]*block-job-description[^'\"]*\1[^>]*>[\s\S]*?<div\b[^>]*class=(['\"])[^'\"]*block-content[^'\"]*\2[^>]*>([\s\S]*?)</div>\s*</div>",
        page_html,
        re.I | re.S,
    )
    if m:
        text = _strip_tags(m.group(3))
        if text:
            return text

    desc = _json_string_field(page_html, "description")
    if desc:
        return _clean_text(desc)

    m = re.search(r"<div\b[^>]*class=(['\"])[^'\"]*main-content job-content text-formated[^'\"]*\1[^>]*>([\s\S]*?)(?:<div\b[^>]*class=(['\"])[^'\"]*(?:cms-block|job-apply|jobs-list|footer)[^'\"]*\3|</body>)", page_html, re.I | re.S)
    return _strip_tags(m.group(2)) if m else None


def _extract_apply_url(page_html, fallback_url):
    m = re.search(r"<a\b([^>]*\bclass=(['\"])[^'\"]*btn-job-apply[^'\"]*\2[^>]*)>", page_html, re.I | re.S)
    href = _attr(m.group(1), "href") if m else None
    return urljoin(fallback_url, href) if href else None


def _make_job(**kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v is not None and v != ""}
    try:
        return Job(**kwargs)
    except NameError:
        return kwargs


def crawl(ctx):
    index_html = ctx.fetch(SITE)
    for item in _extract_index_jobs(index_html):
        page_html = ctx.fetch(item["url"])
        title = _json_string_field(page_html, "title") or item.get("title")
        location = _extract_location(page_html)
        description = _extract_description(page_html)
        posted_at = _json_string_field(page_html, "datePosted")
        external_id = None
        m = re.search(r'data-job-reference\s*=\s*([\'"])(.*?)\1', page_html, re.I | re.S)
        if m:
            external_id = html.unescape(m.group(2)).strip() or None

        yield _make_job(
            source=SITE,
            url=item["url"],
            title=title,
            company=COMPANY,
            location=location,
            description=description,
            employment_type=item.get("employment_type"),
            department=item.get("department"),
            posted_at=posted_at,
            external_id=external_id,
            apply_url=_extract_apply_url(page_html, item["url"]),
        )
