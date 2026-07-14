
import re
import html

SITE = "https://kubeshop.io/careers"
EXTRA_HOSTS = []


def _text(fragment, block_breaks=False):
    if block_breaks:
        fragment = re.sub(
            r"</?(?:p|li|ul|ol|h[1-6])\b[^>]*>|<br\s*/?>",
            "\n",
            fragment,
            flags=re.I,
        )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")

    if block_breaks:
        return "\n".join(
            line
            for line in (
                re.sub(r"[ \t\r\f\v]+", " ", line).strip()
                for line in fragment.splitlines()
            )
            if line
        )

    return re.sub(r"\s+", " ", fragment).strip()


def crawl(ctx):
    page = ctx.fetch(SITE)

    department_pattern = re.compile(
        r'<div\b[^>]*class="[^"]*\bcareers__depart-titile\b[^"]*"[^>]*>'
        r"(?P<department>.*?)</div>",
        re.I | re.S,
    )
    role_pattern = re.compile(
        r'<div\b[^>]*class="[^"]*\bfaq_accordion\b[^"]*\bis-careers\b[^"]*"[^>]*>'
        r'.*?<div\b[^>]*class="[^"]*\bfaq_question\b[^"]*"[^>]*>'
        r".*?<h4\b[^>]*>(?P<title>.*?)</h4>"
        r'.*?<div\b[^>]*class="[^"]*\bw-richtext\b[^"]*"[^>]*>'
        r"(?P<body>.*?)</div>\s*</div>\s*</div>",
        re.I | re.S,
    )

    departments = [
        (match.start(), _text(match.group("department")))
        for match in department_pattern.finditer(page)
    ]

    for match in role_pattern.finditer(page):
        title = _text(match.group("title"))
        description = _text(match.group("body"), block_breaks=True)
        location_match = re.search(r"\(([^()]+)\)\s*$", title)

        if not title or not description or not location_match:
            continue

        job = {
            "url": SITE,
            "title": title,
            "company": "Kubeshop",
            "location": location_match.group(1).strip(),
            "description": description,
            "source": SITE,
        }

        preceding_departments = [
            department
            for position, department in departments
            if position < match.start() and department
        ]
        if preceding_departments:
            job["department"] = preceding_departments[-1]

        mailto_match = re.search(
            r'href=["\'](mailto:[^"\']+)["\']',
            match.group("body"),
            flags=re.I,
        )
        if mailto_match:
            job["apply_url"] = html.unescape(mailto_match.group(1))

        yield job
