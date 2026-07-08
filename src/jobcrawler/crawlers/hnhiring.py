
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://hnhiring.com/"
EXTRA_HOSTS = []

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

TAG_RE = re.compile(r"<[^>]+>")
BLOCK_RE = re.compile(r'<li class="job[^"]*">(.*?)(?=<li class="job|\</ul>)', re.I | re.S)
BODY_RE = re.compile(r'<div class="body">(.*?)</div>\s*</div>\s*</li>', re.I | re.S)
USER_TIME_RE = re.compile(r'<span class="gray right type-info">(.*?)</span>', re.I | re.S)

def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, method="GET")

def _text(html):
    html = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    html = re.sub(r"(?i)</\s*p\s*>", "\n", html)
    html = re.sub(r"(?i)<\s*p[^>]*>", "\n", html)
    html = TAG_RE.sub(" ", html)
    html = unescape(html)
    html = html.replace("\xa0", " ")
    html = re.sub(r"[ \t\r\f\v]+", " ", html)
    html = re.sub(r" *\n *", "\n", html)
    return re.sub(r"\n{3,}", "\n\n", html).strip()

def _links(html):
    return [unescape(m) for m in re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I)]

def _monthly_urls(sitemap_xml):
    urls = re.findall(r"<loc>\s*(https://hnhiring\.com/([a-z]+)-(\d{4}))\s*</loc>", sitemap_xml, re.I)
    dated = []
    for url, month, year in urls:
        month_num = MONTHS.get(month.lower())
        if month_num:
            dated.append((int(year), month_num, url))
    dated.sort(reverse=True)
    return [u for _, _, u in dated]

def _first_line(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")

def _parts_from_header(header):
    return [p.strip(" -\t") for p in header.split("|") if p.strip(" -\t")]

def _location(parts, text):
    keywords = re.compile(
        r"\b(remote|onsite|on-site|hybrid|visa|us|usa|u\.s\.|canada|europe|eu|uk|"
        r"united states|nyc|sf|san francisco|seattle|austin|boston|london|berlin|"
        r"paris|amsterdam|toronto|india|apac|emea|latam)\b",
        re.I,
    )
    locs = [p for p in parts[2:] if keywords.search(p)]
    if locs:
        return " | ".join(locs[:3])
    if re.search(r"\bremote\b", text, re.I):
        return "Remote"
    return "Unspecified"

def _workplace_type(text):
    has_remote = re.search(r"\bremote\b", text, re.I)
    has_onsite = re.search(r"\b(on-?site|in office)\b", text, re.I)
    has_hybrid = re.search(r"\bhybrid\b", text, re.I)
    if has_hybrid:
        return "hybrid"
    if has_remote and has_onsite:
        return "hybrid"
    if has_remote:
        return "remote"
    if has_onsite:
        return "onsite"
    return None

def _employment_type(text):
    if re.search(r"\bfull[- ]time\b", text, re.I):
        return "Full-time"
    if re.search(r"\bpart[- ]time\b", text, re.I):
        return "Part-time"
    if re.search(r"\b(contract|contractor|freelance)\b", text, re.I):
        return "Contract"
    if re.search(r"\bintern(ship)?\b", text, re.I):
        return "Internship"
    return None

def _title(parts):
    if len(parts) >= 2:
        return parts[1]
    if parts:
        return parts[0]
    return None

def crawl(ctx):
    sitemap = _fetch(ctx, urljoin(SITE, "sitemap.xml"))
    month_urls = _monthly_urls(sitemap)
    if not month_urls:
        month_urls = [SITE]

    seen = set()
    # The latest monthly page is the current whole-board index; it contains all visible and hidden jobs.
    for page_url in month_urls[:1]:
        page = _fetch(ctx, page_url)
        blocks = BLOCK_RE.findall(page)
        for index, block in enumerate(blocks, 1):
            body_match = BODY_RE.search(block)
            body_html = body_match.group(1) if body_match else block
            description = _text(body_html)
            if not description:
                continue

            header = _first_line(description)
            parts = _parts_from_header(header)
            company = parts[0] if parts else "Hacker News"
            title = _title(parts)
            if not title:
                continue

            key = re.sub(r"\W+", " ", (company + " " + title + " " + description[:300]).lower()).strip()
            if key in seen:
                continue
            seen.add(key)

            links = _links(body_html)
            apply_url = None
            for link in links:
                if link.startswith("#"):
                    continue
                if "news.ycombinator.com/user" in link:
                    continue
                apply_url = link
                break

            job = {
                "url": page_url + "#job-" + str(index),
                "title": title,
                "company": company,
                "location": _location(parts, description),
                "description": description,
                "source": SITE,
                "external_id": page_url.rsplit("/", 1)[-1] + "-" + str(index),
            }

            wt = _workplace_type(description)
            if wt:
                job["workplace_type"] = wt
            et = _employment_type(description)
            if et:
                job["employment_type"] = et
            if len(parts) >= 3 and not re.search(r"\b(remote|onsite|on-site|hybrid|full[- ]time|part[- ]time|contract)\b", parts[2], re.I):
                job["department"] = parts[2]
            if apply_url:
                job["apply_url"] = apply_url
            time_match = USER_TIME_RE.search(block)
            if time_match:
                # Relative time is source text but not a stable date, so keep it in the description only.
                pass

            yield job
