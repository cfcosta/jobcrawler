
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://xata.io/careers"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(r"<(?:br|/p|/h[1-6]|/li|/ul|/ol)\b[^>]*>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<li\b[^>]*>", "\n- ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    lines = []
    for line in unescape(fragment).splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _embedded_value(page, key):
    match = re.search(
        r'\\"' + re.escape(key) + r'\\":(?:\\"([^"\\]*)\\"|null)',
        page,
    )
    return unescape(match.group(1)) if match and match.group(1) is not None else None


def crawl(ctx):
    landing = ctx.fetch(SITE)
    roles = []
    seen = set()

    for match in re.finditer(
        r'<a\b[^>]*href=["\'](/careers/\d+)/?["\'][^>]*>(.*?)</a>',
        landing,
        flags=re.I | re.S,
    ):
        href, body = match.groups()
        title_match = re.search(
            r'<span\b[^>]*class=["\'][^"\']*\btext-foreground\b[^"\']*'
            r'\bgroup-hover:underline\b[^"\']*["\'][^>]*>(.*?)</span>',
            body,
            flags=re.I | re.S,
        )
        location_match = re.search(
            r'<span\b[^>]*class=["\'][^"\']*\btext-muted-foreground\b'
            r'[^"\']*["\'][^>]*>(.*?)</span>',
            body,
            flags=re.I | re.S,
        )
        if title_match and location_match:
            roles.append((
                href,
                _text(title_match.group(1)),
                _text(location_match.group(1)),
            ))

    for href, listed_title, location in roles:
        url = urljoin(SITE, href)
        if url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        status = _embedded_value(page, "status")
        if status is not None and status != "open":
            continue

        title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, flags=re.I | re.S)
        description_match = re.search(
            r'<div\b[^>]*class=["\'][^"\']*\bprose\b[^"\']*["\'][^>]*>'
            r'(.*?)</div>',
            page,
            flags=re.I | re.S,
        )
        if not description_match:
            continue

        title = _text(title_match.group(1)) if title_match else listed_title
        description = _text(description_match.group(1))
        if not title or not location or not description:
            continue

        apply_match = re.search(
            r'href=["\'](https://[^"\']+\.teamtailor\.com/jobs/'
            r'\d+-[^"\']+/applications/new)(?:\?[^"\']*)?["\']',
            page,
            flags=re.I,
        )
        job_id = re.search(r"/careers/(\d+)/?$", url)
        remote_status = _embedded_value(page, "remote-status")

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Xata",
            "location": location,
            "description": description,
            "external_id": job_id.group(1) if job_id else None,
            "apply_url": unescape(apply_match.group(1)) if apply_match else None,
            "posted_at": _embedded_value(page, "created-at"),
            "employment_type": _embedded_value(page, "employment-type"),
            "workplace_type": "remote" if remote_status == "fully" else remote_status,
        }
        yield {key: value for key, value in fields.items() if value is not None}
