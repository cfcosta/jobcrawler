import re
import html
from urllib.parse import urljoin

SITE = "https://rustlang.com.br/vagas/"
EXTRA_HOSTS = []


def _fetch_text(ctx, url):
    res = ctx.fetch(url)
    return res.text if hasattr(res, "text") else str(res)


def _clean_text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(
        r"<(br|/p|/li|/h[1-6]|/div|/ul|/ol)\b[^>]*>", "\n", value, flags=re.I
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _first(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text, flags)
    if not m:
        return None
    for g in m.groups():
        if g is not None:
            return html.unescape(g).strip()
    return None


def _feed_jobs(ctx):
    xml = _fetch_text(ctx, urljoin(SITE, "/index.xml"))
    jobs = {}
    for item in re.findall(r"<item>(.*?)</item>", xml, re.I | re.S):
        url = _first(r"<link>(.*?)</link>", item)
        if not url or not re.match(
            r"^https://rustlang\.com\.br/vagas/[A-Za-z0-9]+-[^/]+/$", url
        ):
            continue
        desc = _clean_text(_first(r"<description>(.*?)</description>", item))
        jobs[url] = {"description": desc}
    return jobs


def _card_parts(body):
    parts = re.split(
        r'<div\b[^>]*class=(?:"[^"]*\bvaga-card\b[^"]*"|[^\s>]*\bvaga-card\b[^\s>]*)[^>]*>',
        body,
        flags=re.I,
    )
    for part in parts[1:]:
        part = part.split("<nav class=pagination", 1)[0]
        yield part


def _discover_cards(ctx):
    seen = set()
    page_url = SITE
    for _ in range(80):
        body = _fetch_text(ctx, page_url)

        for card in _card_parts(body):
            title = _clean_text(
                _first(
                    r'<h3\b[^>]*class=(?:"[^"]*\bvaga-title\b[^"]*"|[^\s>]*\bvaga-title\b[^\s>]*)[^>]*>(.*?)</h3>',
                    card,
                )
            )
            if not title:
                continue

            href = _first(
                r'<a\b[^>]*href=(?:"([^"]+)"|\'([^\']+)\'|([^>\s]+))[^>]*class=(?:"[^"]*\bvaga-view\b|[^\s>]*\bvaga-view\b)',
                card,
            )
            if not href:
                href = _first(
                    r'<a\b[^>]*href=(?:"([^"]+)"|\'([^\']+)\'|([^>\s]+))', card
                )
            if not href:
                continue

            url = urljoin(page_url, href)
            if (
                not re.match(
                    r"^https://rustlang\.com\.br/vagas/[A-Za-z0-9]+-[^/]+/$", url
                )
                or url in seen
            ):
                continue
            seen.add(url)

            company = _clean_text(
                _first(
                    r'<span\b[^>]*class=(?:"[^"]*\bvaga-company\b[^"]*"|[^\s>]*\bvaga-company\b[^\s>]*)[^>]*>(.*?)</span>',
                    card,
                )
            )
            location = _clean_text(
                _first(
                    r'<a\b[^>]*class=(?:"[^"]*\bvaga-location\b[^"]*"|[^\s>]*\bvaga-location\b[^\s>]*)[^>]*>(.*?)</a>',
                    card,
                )
            )

            badge_text = " ".join(
                [
                    _clean_text(x) or ""
                    for x in re.findall(
                        r'<a\b[^>]*class=(?:"[^"]*\bvaga-badge\b[^"]*"|[^\s>]*\bvaga-badge\b[^\s>]*)[^>]*>(.*?)</a>',
                        card,
                        re.I | re.S,
                    )
                ]
            ).lower()
            workplace_type = None
            if "remoto" in badge_text:
                workplace_type = "remote"
            elif "híbrido" in badge_text or "hibrido" in badge_text:
                workplace_type = "hybrid"
            elif "presencial" in badge_text:
                workplace_type = "onsite"

            slug = url.rstrip("/").rsplit("/", 1)[-1]
            yield {
                "url": url,
                "title": title,
                "company": company,
                "location": location,
                "workplace_type": workplace_type,
                "external_id": slug.split("-", 1)[0],
            }

        next_href = _first(
            r'<a\b[^>]*href=(?:"([^"]+)"|\'([^\']+)\'|([^>\s]+))[^>]*class=(?:"[^"]*\bnext\b[^"]*"|[^\s>]*\bnext\b[^\s>]*)[^>]*>\s*Pr',
            body,
        )
        if not next_href:
            break
        page_url = urljoin(page_url, next_href)


def _compact(data):
    return {k: v for k, v in data.items() if v not in (None, "")}


def crawl(ctx):
    feed = _feed_jobs(ctx)
    for card in _discover_cards(ctx):
        extra = feed.get(card["url"], {})
        job = {}
        job.update(card)
        job.update(extra)
        job["source"] = SITE
        if (
            job.get("title")
            and job.get("company")
            and job.get("location")
            and job.get("description")
        ):
            yield _compact(job)
