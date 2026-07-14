import html
import json
import re
from urllib.parse import urljoin

SITE = "https://www.causely.ai/careers"
EXTRA_HOSTS = []


def _decode_js_string(value):
    try:
        return json.loads('"' + value + '"')
    except (ValueError, json.JSONDecodeError):
        return value


def _slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "open-role"


def crawl(ctx):
    page = ctx.fetch(SITE)
    script_urls = re.findall(
        r'<script[^>]+src=["\']([^"\']+)["\']',
        page,
        flags=re.IGNORECASE,
    )
    seen = set()

    for script_url in script_urls:
        script = ctx.fetch(urljoin(SITE, html.unescape(script_url)))
        if "jobDescription" not in script or "Our open roles" not in script:
            continue

        roles = re.findall(
            r'\{title:"((?:\\.|[^"\\])*)",description:`([\s\S]*?)`\}',
            script,
        )
        for raw_title, raw_description in roles:
            title = _decode_js_string(raw_title).strip()
            description = html.unescape(raw_description).strip()
            if not title or not description or title in seen:
                continue
            seen.add(title)
            yield {
                "url": SITE + "#" + _slug(title),
                "title": title,
                "company": "Causely",
                "location": "Remote",
                "source": SITE,
                "description": description,
                **(
                    {
                        "employment_type": "Full-time",
                        "workplace_type": "Remote",
                    }
                    if re.search(r"\bfull-time role\b", description, re.IGNORECASE)
                    and re.search(r"\bremote role\b", description, re.IGNORECASE)
                    else {}
                ),
            }
