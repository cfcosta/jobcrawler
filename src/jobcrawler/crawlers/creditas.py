
import html
import json
import re


SITE = "https://creditas.gupy.io/"
EXTRA_HOSTS = []


def _response_text(response):
    return response.text if hasattr(response, "text") else response


def _next_data(document):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Gupy page does not contain embedded job data")
    return json.loads(match.group(1))


def _plain_text(markup):
    if not markup:
        return None

    value = re.sub(
        r"<\s*br\s*/?\s*>|</?\s*(?:p|div|section|article|h[1-6]|ul|ol|li|"
        r"blockquote|pre|table|tr|td|th)\b[^>]*>",
        "\n",
        markup,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _location(job, listing):
    address = job.get("addressLine")
    if address:
        return address

    workplace = listing.get("workplace") or {}
    fields = workplace.get("address") or {}
    parts = []
    for key in ("city", "state", "country"):
        value = fields.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def _employment_type(value):
    return {
        "vacancy_type_effective": "effective",
        "vacancy_type_internship": "internship",
    }.get(value, value)


def crawl(ctx):
    homepage = _response_text(ctx.fetch(SITE))
    page_props = _next_data(homepage)["props"]["pageProps"]
    seen = set()

    for listing in page_props.get("jobs") or []:
        job_id = listing.get("id")
        if job_id is None or job_id in seen:
            continue
        seen.add(job_id)

        detail_url = SITE + "jobs/" + str(job_id)
        document = _response_text(ctx.fetch(detail_url))
        detail_props = _next_data(document)["props"]["pageProps"]
        job = detail_props["job"]

        if job.get("status") != "published":
            continue

        sections = []
        for key in (
            "description",
            "responsibilities",
            "prerequisites",
            "relevantExperiences",
        ):
            section = _plain_text(job.get(key))
            if section:
                sections.append(section)

        canonical_url = detail_props.get("canonicalUrl") or detail_url
        workplace = listing.get("workplace") or {}
        fields = {
            "url": canonical_url,
            "source": SITE,
            "apply_url": canonical_url,
            "title": job.get("name") or listing.get("title"),
            "company": "Creditas",
            "location": _location(job, listing),
            "description": "\n\n".join(sections),
            "external_id": str(job_id),
            "department": listing.get("department"),
            "employment_type": _employment_type(
                job.get("jobType") or listing.get("type")
            ),
            "workplace_type": (
                job.get("workplaceType") or workplace.get("workplaceType")
            ),
            "posted_at": job.get("publishedAt"),
            "expires_at": job.get("expiresAt"),
        }
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
