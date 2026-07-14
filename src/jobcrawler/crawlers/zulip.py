
import html
import re

SITE = "https://zulip.com/jobs/"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    fragment = re.sub(
        r"(?i)</(?:p|div|h1|h2|h3|li|ul|ol|blockquote|section)>",
        "\n",
        fragment,
    )
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment).replace("\xa0", " ")

    lines = []
    for line in fragment.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    page = ctx.fetch(SITE)
    lower_page = page.lower()

    section_start = lower_page.find('<h1 id="open-positions"')
    if section_start < 0:
        return

    section_end = lower_page.find('<h1 id="how-we-work"', section_start)
    if section_end < 0:
        section_end = len(page)
    section = page[section_start:section_end]

    first_role = re.search(r"(?is)<h2\b", section)
    intro = section[:first_role.start()] if first_role else section
    intro_paragraphs = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", intro)

    application_instructions = None
    engineer_invitation = None
    remote_statement = None
    for paragraph in intro_paragraphs:
        paragraph_text = _text(paragraph)
        lowered = paragraph_text.lower()
        if "to apply for an open position" in lowered:
            application_instructions = paragraph_text
        elif "if you are an experienced" in lowered and "engineer" in lowered:
            engineer_invitation = paragraph_text
        elif "all openings are remote" in lowered:
            remote_statement = paragraph_text

    if engineer_invitation:
        description_parts = []
        if application_instructions:
            description_parts.append(application_instructions)
        description_parts.append(engineer_invitation)
        if remote_statement:
            description_parts.append(remote_statement)

        yield {
            "url": SITE + "#open-positions",
            "title": "Experienced engineer",
            "company": "Zulip",
            "location": "Remote",
            "workplace_type": "remote",
            "description": "\n\n".join(description_parts),
            "apply_url": "mailto:jobs+engineer@zulip.com",
            "source": SITE,
        }

    headings = list(re.finditer(
        r'''(?is)<h2\b[^>]*\bid=["']([^"']+)["'][^>]*>(.*?)</h2>''',
        section,
    ))
    for index, heading in enumerate(headings):
        block_end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        block = section[heading.start():block_end]
        description = _text(block)
        lowered = re.sub(r"\s+", " ", description).lower()

        if "not currently accepting new applications" in lowered:
            continue

        title = _text(heading.group(2))
        role_id = heading.group(1)
        mailto = re.search(r'''(?is)href=["'](mailto:[^"']+)["']''', block)

        job = {
            "url": SITE + "#" + role_id,
            "title": title,
            "company": "Zulip",
            "location": "Remote",
            "workplace_type": "remote",
            "description": description,
            "source": SITE,
        }
        if mailto:
            job["apply_url"] = html.unescape(mailto.group(1))
        yield job
