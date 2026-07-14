import html
import re


SITE = "https://www.amberdata.io/careers"
EXTRA_HOSTS = []


SECTIONS = (
    ("engineering-jobs", "Engineering"),
    ("sales-jobs", "Sales"),
)


def text_content(fragment):
    fragment = re.sub(r"(?is)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)<li\b[^>]*>", "- ", fragment)
    fragment = re.sub(r"(?is)</(?:p|li|ul|ol|h[1-6])\s*>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")
    lines = []
    for line in fragment.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    page = ctx.fetch(SITE)

    for index, section in enumerate(SECTIONS):
        section_id, department = section
        start_match = re.search(
            r'''(?is)<a\b[^>]*\bid=["']''' + re.escape(section_id)
            + r'''["'][^>]*>.*?<h3\b[^>]*>.*?</h3>''',
            page,
        )
        if not start_match:
            continue

        start = start_match.end()
        if index + 1 < len(SECTIONS):
            next_id = SECTIONS[index + 1][0]
            next_match = re.search(
                r'''(?is)<a\b[^>]*\bid=["']''' + re.escape(next_id)
                + r'''["'][^>]*>''',
                page[start:],
            )
            end = start + next_match.start() if next_match else len(page)
        else:
            module_end = re.search(
                r'''(?is)<p\b[^>]*>\s*<span\b[^>]*>\s*'''
                r'''<a\b[^>]*href=["']#top["'][^>]*>Back to Top</a>'''
                r'''.*?</p>''',
                page[start:],
            )
            end = start + module_end.start() if module_end else len(page)

        section_html = page[start:end]
        title_match = re.search(r"(?is)<h5\b[^>]*>(.*?)</h5>", section_html)
        if not title_match:
            continue

        title = text_content(title_match.group(1))
        body = section_html[title_match.end():]
        back_to_top = re.search(
            r'''(?is)<p\b[^>]*>(?:(?!</p>).)*<a\b[^>]*href=["']#top["'][^>]*>'''
            r'''Back to Top</a>.*?</p>''',
            body,
        )
        if back_to_top:
            body = body[:back_to_top.start()]

        description = text_content(body)
        if not title or not description:
            continue

        yield {
            "url": SITE + "#" + section_id,
            "title": title,
            "company": "Amberdata",
            "location": "Remote",
            "description": description,
            "workplace_type": "remote",
            "department": department,
            "apply_url": "mailto:jobs@amberdata.io",
            "source": SITE,
        }
