import html
import json
import re

SITE = "https://slite.com/jobs"
EXTRA_HOSTS = []


def _page_context(page):
    match = re.search(
        r'<script\b(?=[^>]*\bid=["\']vike_pageContext["\'])[^>]*>(.*?)</script\s*>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(html.unescape(raw))


def _portable_text(sections):
    output = []

    for section in sections or []:
        if not isinstance(section, dict):
            continue

        title = section.get("title")
        if isinstance(title, str) and title.strip():
            output.append(title.strip())

        for block in section.get("content") or []:
            if not isinstance(block, dict):
                continue

            parts = []
            for child in block.get("children") or []:
                if not isinstance(child, dict):
                    continue
                if child.get("_type") == "span":
                    parts.append(child.get("text") or "")

            text = "".join(parts).strip()
            if not text:
                continue

            if block.get("listItem") == "bullet":
                text = "- " + text
            elif block.get("listItem") == "number":
                text = "1. " + text
            output.append(text)

        output.append("")

    return "\n".join(output).strip()


def _clean_url(value):
    if not isinstance(value, str):
        return None
    value = value.replace("\\/", "/").strip()
    return value or None


def crawl(ctx):
    index_context = _page_context(ctx.fetch(SITE))
    if not isinstance(index_context, dict):
        return

    roles = index_context.get("data")
    if not isinstance(roles, list):
        return

    seen = set()
    for listing in roles:
        if not isinstance(listing, dict):
            continue

        slug_data = listing.get("slug")
        slug = slug_data.get("current") if isinstance(slug_data, dict) else None
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            continue
        if slug in seen:
            continue
        seen.add(slug)

        url = SITE + "/" + slug
        detail_context = _page_context(ctx.fetch(url))
        if not isinstance(detail_context, dict):
            continue

        data = detail_context.get("data")
        job = data.get("job") if isinstance(data, dict) else None
        if not isinstance(job, dict) or job.get("isActive") is not True:
            continue

        detail_slug = job.get("slug")
        detail_slug = detail_slug.get("current") if isinstance(detail_slug, dict) else None
        if detail_slug != slug:
            continue

        title = job.get("title")
        description = _portable_text(job.get("sections"))
        if not isinstance(title, str) or not title.strip() or not description:
            continue

        category = job.get("category")
        external_id = job.get("_id")
        apply_url = _clean_url(job.get("applyUrl"))

        fields = {
            "url": url,
            "title": title.strip(),
            "company": "Slite",
            "location": "Remote",
            "workplace_type": "remote",
            "description": description,
            "source": SITE,
        }
        if isinstance(category, str) and category.strip():
            fields["department"] = category.strip()
        if isinstance(external_id, str) and external_id.strip():
            fields["external_id"] = external_id.strip()
        if apply_url:
            fields["apply_url"] = apply_url

        yield fields
