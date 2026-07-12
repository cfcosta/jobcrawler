
import re
from html import unescape


SITE = "https://betterstack.com/careers/open-positions"
EXTRA_HOSTS = []


def clean_html(value):
    value = re.sub(r"(?is)<(?:script|style)\b.*?</(?:script|style)>", " ", value or "")
    value = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def target_text(chunk, target):
    match = re.search(
        r"(?is)<[^>]+\bdata-careers-roles-target=['\"]"
        + re.escape(target)
        + r"['\"][^>]*>(.*?)</[^>]+>",
        chunk,
    )
    return clean_html(match.group(1)) if match else None


def crawl(ctx):
    page = ctx.fetch(SITE)
    starts = list(re.finditer(
        r"(?is)<div\b"
        r"(?=[^>]*\bdata-careers-roles-target=['\"]roleContent['\"])"
        r"(?=[^>]*\bdata-role=['\"]([^'\"]+)['\"])[^>]*>",
        page,
    ))

    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(page)
        chunk = page[start.start():end]
        slug = start.group(1)

        if index + 1 == len(starts):
            content_end = re.search(r"(?is)</main>|<footer\b", chunk)
            if content_end:
                chunk = chunk[:content_end.start()]

        title = target_text(chunk, "roleTitle")
        if slug == "fullstack-engineer":
            title = "Full-stack Engineer"

        salary = target_text(chunk, "salaryBase")
        remote = target_text(chunk, "remoteLocation")
        onsite = target_text(chunk, "onsiteLocation")
        locations = [value for value in (remote, onsite) if value]
        location = remote or onsite

        apply_match = re.search(
            r"(?is)<a\b[^>]*\bhref=['\"]"
            r"(https://betterstack\.typeform\.com/apply-[^'\"]+)"
            r"['\"]",
            chunk,
        )

        fields = {
            "url": SITE + "#" + slug,
            "title": title,
            "company": "Better Stack",
            "location": location,
            "locations": locations,
            "description": clean_html(chunk),
            "source": SITE,
            "external_id": slug,
            "apply_url": apply_match.group(1) if apply_match else None,
            "workplace_type": "remote" if remote else None,
            "salary": salary,
        }

        salary_match = re.fullmatch(
            r"\$(\d+(?:\.\d+)?)([KkMm]?)\s*-\s*"
            r"\$(\d+(?:\.\d+)?)([KkMm]?)/year\s*\+\s*equity",
            salary or "",
        )
        if salary_match:
            factors = {"": 1, "k": 1000, "m": 1000000}
            fields["salary_min"] = (
                float(salary_match.group(1))
                * factors[salary_match.group(2).lower()]
            )
            fields["salary_max"] = (
                float(salary_match.group(3))
                * factors[salary_match.group(4).lower()]
            )
            fields["salary_currency"] = "USD"
            fields["salary_period"] = "year"

        yield {key: value for key, value in fields.items() if value is not None}
