
import html
import json
import re
from urllib.parse import urljoin, urlparse

SITE = "https://mergify.com/careers"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(r"<(?:script|style|svg)\b[^>]*>.*?</(?:script|style|svg)>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(?:p|li|h[1-6]|blockquote|dd|dt|div|section|header|aside|ul|ol)>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    value = html.unescape(fragment)
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _json_ld(page):
    for raw in re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    ):
        try:
            data = json.loads(html.unescape(raw))
        except (TypeError, ValueError):
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                return obj
            if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                for item in obj["@graph"]:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        return item
    return {}


def _facts(article):
    facts = {}
    match = re.search(r"<aside\b.*?</aside>", article, re.I | re.S)
    if not match:
        return facts
    for key, value in re.findall(
        r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>",
        match.group(0),
        re.I | re.S,
    ):
        name = _text(key)
        stated = _text(value)
        if name and stated:
            facts[name] = stated
    return facts


def crawl(ctx):
    listing = ctx.fetch(SITE)

    openings_match = re.search(
        r'<section\b[^>]*\bid=["\']openings["\'][^>]*>(.*?)</section>',
        listing,
        re.I | re.S,
    )
    if not openings_match:
        return

    links = []
    for href in re.findall(
        r'<a\b[^>]*href=["\']([^"\']+)["\']',
        openings_match.group(1),
        re.I,
    ):
        url = urljoin(SITE, html.unescape(href))
        parsed = urlparse(url)
        if parsed.netloc == "mergify.com" and re.match(r"^/careers/[^/]+/?$", parsed.path):
            canonical = parsed._replace(query="", fragment="").geturl()
            if canonical not in links:
                links.append(canonical)

    for url in links:
        page = ctx.fetch(url)
        schema = _json_ld(page)

        article_match = re.search(r"<article\b[^>]*>(.*?)</article>", page, re.I | re.S)
        if not article_match:
            continue
        article = article_match.group(1)
        facts = _facts(article)

        title = schema.get("title")
        if not title:
            heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", article, re.I | re.S)
            title = _text(heading.group(1)) if heading else None

        location = facts.get("Location")
        if not location:
            requirements = schema.get("applicantLocationRequirements")
            if isinstance(requirements, dict):
                location = requirements.get("name")

        apply_match = re.search(
            r'href=["\'](https://forms\.mergify\.com/job-apply[^"\']*)',
            article,
            re.I,
        )
        kwargs = {
            "url": url,
            "title": title,
            "company": "Mergify",
            "location": location,
            "description": _text(article),
            "department": facts.get("Team"),
            "employment_type": facts.get("Employment") or schema.get("employmentType"),
            "posted_at": schema.get("datePosted"),
            "expires_at": schema.get("validThrough"),
            "apply_url": html.unescape(apply_match.group(1)) if apply_match else None,
        }
        if schema.get("jobLocationType") == "TELECOMMUTE":
            kwargs["workplace_type"] = "remote"

        kwargs["source"] = SITE
        yield {key: value for key, value in kwargs.items() if value is not None}
