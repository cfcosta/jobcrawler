
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://krisp.ai/careers/"
EXTRA_HOSTS = []


def text(value):
    value = re.sub(r"<!--.*?-->", " ", value or "", flags=re.S)
    value = re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<li\b[^>]*>", "\n- ", value, flags=re.I)
    value = re.sub(
        r"</?(?:p|div|section|article|ul|ol|h1|h2|h3|h4|h5|h6|blockquote|tr|table)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def attr_value(tag, name):
    match = re.search(
        r"\b" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1",
        tag,
        flags=re.I | re.S,
    )
    return unescape(match.group(2)) if match else None


def listing_records(page):
    jobs_area = re.search(
        r'<div\b[^>]*\bid\s*=\s*["\']jobs["\'][^>]*>(.*)</section>',
        page,
        flags=re.I | re.S,
    )
    area = jobs_area.group(1) if jobs_area else page

    headings = list(re.finditer(
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bsubtitle\b[^"\']*\bmb_8\b[^"\']*["\'][^>]*>'
        r'(.*?)</div>',
        area,
        flags=re.I | re.S,
    ))

    records = []
    for index, heading in enumerate(headings):
        department = text(heading.group(1))
        end = headings[index + 1].start() if index + 1 < len(headings) else len(area)
        group = area[heading.end():end]

        anchors = re.finditer(
            r'(<a\b[^>]*href\s*=\s*(["\'])(.*?/jobs/.*?)\2[^>]*>)(.*?)</a>',
            group,
            flags=re.I | re.S,
        )
        for anchor in anchors:
            url = urljoin(SITE, unescape(anchor.group(3)))
            body = anchor.group(4)
            title_match = re.search(
                r'<span\b[^>]*class\s*=\s*["\'][^"\']*\bsubtitle--md\b[^"\']*["\'][^>]*>'
                r'(.*?)</span>',
                body,
                flags=re.I | re.S,
            )
            if not title_match:
                continue

            badges = [
                text(match.group(1))
                for match in re.finditer(
                    r'<div\b[^>]*class\s*=\s*["\'][^"\']*\blocation\b[^"\']*["\'][^>]*>'
                    r'(.*?)</div>',
                    body,
                    flags=re.I | re.S,
                )
                if text(match.group(1))
            ]
            records.append({
                "url": url,
                "title": text(title_match.group(1)),
                "department": department,
                "badges": badges,
            })
    return records


def detail_values(page):
    container = re.search(
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bjob_data_container\b[^"\']*["\'][^>]*>'
        r'(.*?)</section>',
        page,
        flags=re.I | re.S,
    )
    section = container.group(1) if container else ""

    body_start = re.search(
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*\btext_body--md\b'
        r'[^"\']*\bmb_24\b[^"\']*["\'][^>]*>',
        section,
        flags=re.I,
    )
    body = section[body_start.start():] if body_start else section

    button = re.search(
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*\bmt_16\b[^"\']*\bmb_32\b[^"\']*["\'][^>]*>',
        body,
        flags=re.I,
    )
    if button:
        body = body[:button.start()]

    apply_url = None
    for link in re.finditer(r'<a\b[^>]*>.*?</a>', section, flags=re.I | re.S):
        href = attr_value(link.group(0), "href")
        label = text(link.group(0)).lower()
        if href and ("apply" in label or label == "form"):
            candidate = urljoin(SITE, href)
            if "/careers/" not in candidate:
                apply_url = candidate
                break

    return text(body), apply_url


def crawl(ctx):
    page = ctx.fetch(SITE)
    seen = set()

    for record in listing_records(page):
        url = record["url"]
        if url in seen:
            continue
        seen.add(url)

        description, apply_url = detail_values(ctx.fetch(url))
        geo = []
        workplace_type = None
        for badge in record["badges"]:
            if badge.lower() in {"remote", "hybrid", "on-site", "onsite"}:
                workplace_type = badge
            else:
                geo.append(badge)

        job = {
            "url": url,
            "title": record["title"],
            "company": "Krisp",
            "location": geo[0] if geo else None,
            "description": description,
            "source": SITE,
            "department": record["department"],
            "workplace_type": workplace_type,
            "apply_url": apply_url,
        }
        yield {key: value for key, value in job.items() if value is not None}
