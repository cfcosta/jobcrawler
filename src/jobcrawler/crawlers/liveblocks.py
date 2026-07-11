
import html
import re
from urllib.parse import urljoin

SITE = "https://liveblocks.io/careers"
EXTRA_HOSTS = []


def clean_text(fragment):
    if not fragment:
        return None
    value = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", fragment)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|section|article|header|footer|aside|li|ul|ol|h1|h2|h3|h4|dt|dd)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def attr_value(tag, name):
    m = re.search(r'\b%s=(["\'])(.*?)\1' % re.escape(name), tag, re.I | re.S)
    return html.unescape(m.group(2)) if m else None


def first_text(pattern, source):
    m = re.search(pattern, source, re.I | re.S)
    return clean_text(m.group(1)) if m else None


def extract_open_roles(page_html):
    roles = []
    section_start = page_html.find('id="open-roles"')
    if section_start == -1:
        section_start = page_html.find("id='open-roles'")
    section = page_html[section_start:] if section_start != -1 else page_html

    seen = set()
    pattern = r'<a\b[^>]*href=(["\'])(/careers/[^"\']+)\1[^>]*>\s*View role\s*</a>'
    for match in re.finditer(pattern, section, re.I | re.S):
        href = html.unescape(match.group(2))
        if href in seen:
            continue
        seen.add(href)

        before = section[max(0, match.start() - 5000):match.start()]
        row_start = before.rfind("<li")
        row = before[row_start:] if row_start != -1 else before

        title = first_text(r'<p\b[^>]*font-medium[^>]*text-marketing[^>]*>(.*?)</p>', row)
        location = None
        loc_matches = re.findall(r'<p\b[^>]*text-marketing-subtle[^>]*>(.*?)</p>', row, re.I | re.S)
        if loc_matches:
            location = clean_text(loc_matches[-1])

        department = None
        dept_matches = re.findall(r'<h3\b[^>]*text-marketing-subtle[^>]*>(.*?)</h3>', before, re.I | re.S)
        if dept_matches:
            department = clean_text(dept_matches[-1])

        roles.append({
            "url": urljoin(SITE, href),
            "title": title,
            "location": location,
            "department": department,
        })
    return roles


def extract_description(page_html):
    # Detail pages render the whole role body in a markdown div inside the article.
    m = re.search(
        r'<div\b[^>]*class=(["\'])[^"\']*\bmarkdown\b[^"\']*\1[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</article>',
        page_html,
        re.I | re.S,
    )
    if m:
        return clean_text(m.group(2))

    article = re.search(r"<article\b[^>]*>(.*?)</article>", page_html, re.I | re.S)
    fragment = article.group(1) if article else page_html
    h1 = re.search(r"<h1\b[^>]*>.*?</h1>", fragment, re.I | re.S)
    if h1:
        fragment = fragment[h1.end():]
    return clean_text(fragment)


def detail_fields(page_html):
    fields = {}

    title = first_text(r"<h1\b[^>]*>(.*?)</h1>", page_html)
    if title:
        fields["title"] = title

    location = first_text(r"<dt\b[^>]*>\s*Location\s*</dt>\s*<dd\b[^>]*>(.*?)</dd>", page_html)
    if location:
        fields["location"] = location
        fields["locations"] = [location]

    employment_type = first_text(r"<dt\b[^>]*>\s*Type\s*</dt>\s*<dd\b[^>]*>(.*?)</dd>", page_html)
    if employment_type:
        fields["employment_type"] = employment_type

    apply_match = re.search(r'<a\b[^>]*(?:title|aria-label)=(["\'])Apply for .*?\1[^>]*>', page_html, re.I | re.S)
    if apply_match:
        apply_url = attr_value(apply_match.group(0), "href")
        if apply_url:
            fields["apply_url"] = apply_url

    description = extract_description(page_html)
    if description:
        fields["description"] = description

    return fields


def crawl(ctx):
    board_html = ctx.fetch(SITE)
    for role in extract_open_roles(board_html):
        detail_html = ctx.fetch(role["url"])
        job = {
            "url": role["url"],
            "company": "Liveblocks",
            "source": SITE,
        }

        for key in ("title", "location", "department"):
            if role.get(key):
                job[key] = role[key]

        job.update(detail_fields(detail_html))

        cleaned = {}
        for key, value in job.items():
            if value is not None and value != "":
                cleaned[key] = value
        yield cleaned
