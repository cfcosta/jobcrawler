
import re

SITE = "https://cal.com/jobs"
EXTRA_HOSTS = []


def _clean_text(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    replacements = {
        "&nbsp;": " ",
        "&#160;": " ",
        "&amp;": "&",
        "&quot;": '"',
        "&#x27;": "'",
        "&#39;": "'",
        "&lt;": "<",
        "&gt;": ">",
        "&#x2F;": "/",
    }
    for encoded, decoded in replacements.items():
        value = value.replace(encoded, decoded)
    value = re.sub(
        r"&#(\d+);",
        lambda match: chr(int(match.group(1))),
        value,
    )
    value = re.sub(
        r"&#x([0-9a-f]+);",
        lambda match: chr(int(match.group(1), 16)),
        value,
        flags=re.I,
    )
    return " ".join(value.split())


def _block_lines(fragment):
    lines = []
    for match in re.finditer(
        r"<(h[1-6]|p|li)\b[^>]*>(.*?)</\1\s*>",
        fragment,
        flags=re.I | re.S,
    ):
        text = _clean_text(match.group(2))
        if text and (not lines or text != lines[-1]):
            lines.append(text)
    return lines


def _cards(html):
    cards = []
    pattern = (
        r'<a\b(?=[^>]*data-framer-name=["\']Vacancy Card["\'])'
        r'(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>(.*?)</a\s*>'
    )
    for match in re.finditer(pattern, html, flags=re.I | re.S):
        lines = _block_lines(match.group(2))
        if len(lines) >= 6:
            cards.append((match.group(1), lines))
    return cards


def _main_fragment(html):
    match = re.search(
        r'<(?:main|div)\b[^>]*data-framer-name=["\']Main["\'][^>]*>',
        html,
        flags=re.I,
    )
    if not match:
        return ""
    fragment = html[match.end():]
    footer = re.search(
        r'<(?:footer|div)\b[^>]*data-framer-name=["\'][^"\']*Footer',
        fragment,
        flags=re.I,
    )
    if footer:
        fragment = fragment[:footer.start()]
    return fragment


def _description_and_apply(html, page_url):
    fragment = _main_fragment(html)
    lines = _block_lines(fragment)
    if len(lines) < 4:
        return None, None

    description = []
    apply_count = 0
    for line in lines[1:]:
        if line.casefold() == "apply now":
            apply_count += 1
            if apply_count == 2:
                break
            continue
        description.append(line)

    apply_url = None
    for match in re.finditer(
        r'<a\b(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>(.*?)</a\s*>',
        fragment,
        flags=re.I | re.S,
    ):
        if _clean_text(match.group(2)).casefold() == "apply now":
            href = match.group(1)
            if href.startswith(("mailto:", "https://", "http://")):
                apply_url = href
            elif href.startswith("/"):
                apply_url = "https://cal.com" + href
            else:
                apply_url = page_url.rsplit("/", 1)[0] + "/" + href
            break

    description = "\n".join(description).strip()
    return description or None, apply_url


def _workplace_type(location):
    lowered = location.casefold()
    if "hybrid" in lowered:
        return "hybrid"
    if "remote" in lowered:
        return "remote"
    if "on-site" in lowered or "onsite" in lowered:
        return "on-site"
    return None


def crawl(ctx):
    board_html = ctx.fetch(SITE)
    seen = set()

    for href, lines in _cards(board_html):
        if href.startswith("./"):
            url = SITE.rsplit("/", 1)[0] + "/" + href[2:]
        elif href.startswith("/"):
            url = "https://cal.com" + href
        elif href.startswith("https://cal.com/"):
            url = href
        else:
            continue

        if not url.startswith("https://cal.com/jobs/") or url in seen:
            continue
        seen.add(url)

        headline = lines[0]
        title = headline.split(" · ", 1)[0].strip()
        department, location, employment_type, salary = lines[-4:]
        if not title or not location:
            continue

        detail_html = ctx.fetch(url)
        description, apply_url = _description_and_apply(detail_html, url)
        if not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Cal.com",
            "location": location,
            "description": description,
            "source": SITE,
            "department": department,
            "employment_type": employment_type,
            "salary": salary,
        }
        workplace_type = _workplace_type(location)
        if workplace_type:
            job["workplace_type"] = workplace_type
        if apply_url:
            job["apply_url"] = apply_url
        yield job
