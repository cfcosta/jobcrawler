import html
import re

SITE = "https://www.rustjobs.fyi/"
EXTRA_HOSTS = []


def _clean(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def crawl(ctx):
    page = ctx.fetch(SITE)
    for block in re.findall(r"<li>(.*?)</li>", page, flags=re.S):
        m = re.search(
            r'<a\s+href="([^"]+)"[^>]*class="text-white"[^>]*>(.*?)</a>',
            block,
            flags=re.S,
        )
        if not m:
            continue

        apply_url = html.unescape(m.group(1))
        body = m.group(2)

        company_m = re.search(
            r'<span class="text-red-300">(.*?)</span>', body, flags=re.S
        )
        location_m = re.search(
            r'<span class="text-gray-500 text-sm">(.*?)</span>', body, flags=re.S
        )
        if not (company_m and location_m):
            continue

        company = _clean(company_m.group(1))
        location = _clean(location_m.group(1))

        title_part = re.sub(
            r'<span class="text-red-300">.*?</span>\s*<span class="text-gray-500 text-sm">.*?</span>',
            "",
            body,
            flags=re.S,
        )
        title_part = _clean(title_part)
        if title_part.endswith(" at"):
            title = title_part[:-3].strip()
        else:
            title = title_part.rsplit(" at ", 1)[0].strip()

        if not title or not company or not location:
            continue

        loc_l = location.lower()
        workplace_type = None
        if "remote" in loc_l:
            workplace_type = (
                "remote"
                if loc_l.strip() == "remote"
                or "/ remote" in loc_l
                or "remote," in loc_l
                else "hybrid"
            )

        description = (
            f"{title} at {company}. Location: {location}. "
            "This role is listed on rustjobs.fyi, a curated job board for positions working with the Rust programming language. "
            "The source board does not publish a longer job description; it links directly to the employer application page. "
            f"Apply at: {apply_url}"
        )

        job = {
            "url": apply_url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "apply_url": apply_url,
            "external_id": apply_url,
        }
        if workplace_type:
            job["workplace_type"] = workplace_type

        yield job
