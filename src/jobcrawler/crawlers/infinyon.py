
import re
from html import unescape
from urllib.parse import urljoin


SITE = "https://www.infinyon.com/careers"
EXTRA_HOSTS = []


def text_content(fragment):
    fragment = re.sub(r"(?is)<(?:script|style)\b.*?</(?:script|style)>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:h[1-6]|p|li|ul|ol|div|section|tr)>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "- ", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = unescape(fragment).replace("\xa0", " ")

    lines = []
    for line in fragment.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def attribute(tag, name):
    match = re.search(
        r'''(?is)\b%s\s*=\s*(["'])(.*?)\1''' % re.escape(name),
        tag,
    )
    return unescape(match.group(2)).strip() if match else None


def crawl(ctx):
    listing_html = ctx.fetch(SITE)
    articles = re.findall(r"(?is)<article\b[^>]*>(.*?)</article>", listing_html)

    seen = set()
    for article in articles:
        heading = re.search(r"(?is)<h3\b[^>]*>(.*?)</h3>", article)
        if not heading:
            continue

        link = re.search(
            r'''(?is)<a\b[^>]*href\s*=\s*(["'])(.*?)\1''',
            heading.group(1),
        )
        if not link:
            continue

        url = urljoin(SITE.rstrip("/") + "/", unescape(link.group(2)).strip())
        title = text_content(heading.group(1))
        if not title or url in seen:
            continue
        seen.add(url)

        metadata = []
        for span_tag, span_body in re.findall(
            r"(?is)(<span\b[^>]*>)(.*?)</span>",
            article,
        ):
            classes = (attribute(span_tag, "class") or "").split()
            if "ml-2" in classes and "text-gray-600" in classes:
                value = text_content(span_body)
                if value:
                    metadata.append(value)

        detail_html = ctx.fetch(url)
        prose_start = re.search(
            r'''(?is)<div\b[^>]*class\s*=\s*(["'])[^"']*\bprose\b[^"']*\bprose-quoteless\b[^"']*\1[^>]*>''',
            detail_html,
        )
        if not prose_start:
            continue

        prose_tail = detail_html[prose_start.end():]
        prose_end = re.search(
            r'''(?is)<div\b[^>]*class\s*=\s*(["'])[^"']*\btext-center\b[^"']*\bpy-4\b[^"']*\1[^>]*>''',
            prose_tail,
        )
        prose_html = prose_tail[:prose_end.start()] if prose_end else prose_tail
        description = text_content(prose_html)
        if not description:
            continue

        apply_match = re.search(
            r"(?is)<a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>\s*<button\b[^>]*>\s*Apply\s+Now\s*</button>",
            detail_html,
        )

        fields = {
            "url": url,
            "title": title,
            "company": "Infinyon",
            "location": metadata[0] if metadata else None,
            "description": description,
            "source": SITE,
        }
        if len(metadata) > 1:
            fields["employment_type"] = metadata[1]
        if apply_match:
            fields["apply_url"] = urljoin(
                url,
                unescape(apply_match.group(1)).strip(),
            )

        yield fields
