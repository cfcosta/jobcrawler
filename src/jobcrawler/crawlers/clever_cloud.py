import html
import re


SITE = "https://www.clever.cloud/jobs/"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(
        r'(?is)<div\b[^>]*class=["\'][^"\']*\bwp-block-button\b[^"\']*["\'][^>]*>.*',
        "",
        fragment,
    )
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"(?i)</(?:p|li|ul|ol|div|h[1-6])\s*>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")

    lines = []
    for line in fragment.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "position"


def crawl(ctx):
    page = ctx.fetch(SITE)
    body = page if isinstance(page, str) else page.text

    cards = re.findall(
        r'''(?is)
        <div\b[^>]*class=["'][^"']*\bcard-avantage\b[^"']*\bbig-target\b[^"']*["'][^>]*>
        (.*?)
        (?=<div\b[^>]*class=["'][^"']*\bcard-avantage\b|</section>|<div\b[^>]*class=["'][^"']*\bwp-block-group\b|\Z)
        ''',
        body,
        re.X,
    )

    for card in cards:
        title_match = re.search(
            r'(?is)<h3\b[^>]*class=["\'][^"\']*\bh5\b[^"\']*["\'][^>]*>(.*?)</h3>',
            card,
        )
        snippet_match = re.search(
            r'(?is)<div\b[^>]*class=["\'][^"\']*\bcc-job-snippet\b[^"\']*["\'][^>]*>(.*)',
            card,
        )
        if not title_match or not snippet_match:
            continue

        title = _text(title_match.group(1))
        description = _text(snippet_match.group(1))
        if not title or not description:
            continue

        links = re.findall(r'''(?is)<a\b[^>]*href=["']([^"']+)["']''', card)
        apply_url = next(
            (
                html.unescape(link)
                for link in links
                if link.startswith(("https://", "http://"))
                and "clever.cloud/jobs/" not in link
            ),
            None,
        )

        location = None
        employment_type = None
        workplace_type = None
        first_line = description.splitlines()[0]
        intro = re.match(r"^(.+?)\s+(?:–|—|-)\s+(.+?)$", first_line)
        if intro:
            employment_type = intro.group(1).strip()
            location = intro.group(2).strip()
            workplace_match = re.match(
                r"^(Remote|Hybrid|On-site|Onsite)\b", location, re.I
            )
            if workplace_match:
                workplace_type = workplace_match.group(1)

        if not location:
            continue

        fields = {
            "url": SITE + "#" + _slug(title),
            "title": title,
            "company": "Clever Cloud",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if apply_url:
            fields["apply_url"] = apply_url
        if employment_type:
            fields["employment_type"] = employment_type
        if workplace_type:
            fields["workplace_type"] = workplace_type

        yield fields
