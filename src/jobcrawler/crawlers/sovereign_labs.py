
import re
from html import unescape

SITE = "https://www.sovereign.xyz/join"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def crawl(ctx):
    page = ctx.fetch(SITE)

    heading = re.search(
        r"<h3\b[^>]*>.*?Open Positions.*?</h3>",
        page,
        re.I | re.S,
    )
    if not heading:
        return
    careers_html = page[heading.start():]

    titles = []
    seen = set()
    for match in re.finditer(
        r'data-framer-name="Job Title"[^>]*>.*?'
        r"<p\b[^>]*>(.*?)</p>",
        careers_html,
        re.I | re.S,
    ):
        title = _text(match.group(1))
        if title and title not in seen:
            seen.add(title)
            titles.append(title)

    mission = None
    for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", page, re.I | re.S):
        text = _text(match.group(1))
        if (
            text.startswith("We want to see blockchain applications scale")
            and "Sovereign SDK rollups are easy to build" in text
        ):
            mission = text
            break

    application = None
    for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", careers_html, re.I | re.S):
        text = _text(match.group(1))
        if (
            text.startswith("If you")
            and "interested, please reach out" in text
            and "compensate more than competitively" in text
        ):
            application = text
            break

    description = "\n\n".join(
        part for part in (mission, application) if part
    )
    if not description:
        return

    for title in titles:
        yield {
            "url": SITE,
            "title": title,
            "company": "Sovereign Labs",
            "location": "Not specified",
            "description": description,
            "source": SITE,
        }
