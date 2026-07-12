
import html
import json
import re

SITE = "https://vemproitau.gupy.io/"
EXTRA_HOSTS = ["employability-portal.gupy.io"]

_API = "https://employability-portal.gupy.io/api/v1/jobs"


def _next_data(document):
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        re.S | re.I,
    )
    if not match:
        raise ValueError("Gupy __NEXT_DATA__ payload not found")
    return json.loads(match.group(1))


def _plain(value):
    if not value:
        return None
    if not isinstance(value, str):
        return None

    value = re.sub(
        r"<\s*(?:br|p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"</\s*(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr)\s*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _location(listing):
    workplace = listing.get("workplace") or {}
    address = workplace.get("address") or {}
    parts = [
        address.get("city"),
        address.get("district"),
        address.get("state"),
        address.get("country"),
    ]
    return ", ".join(part for part in parts if part) or "Brasil"


def _first(record, names):
    for name in names:
        value = record.get(name)
        if value is not None and value != "":
            return value
    return None


def _description(record):
    sections = []
    seen = set()

    for field in (
        "description",
        "responsibilities",
        "prerequisites",
        "requirements",
        "qualifications",
        "relevantExperiences",
        "additionalInformation",
        "benefits",
    ):
        text = _plain(record.get(field))
        if text and text not in seen:
            sections.append(text)
            seen.add(text)

    return "\n\n".join(sections).strip()


def crawl(ctx):
    board = _next_data(ctx.fetch(SITE))
    listings = board["props"]["pageProps"]["jobs"]

    open_listings = {}
    order = []
    for listing in listings:
        if listing.get("type") == "vacancy_type_talent_pool":
            continue
        job_id = int(listing["id"])
        open_listings[job_id] = listing
        order.append(job_id)

    records = {}
    offset = 0
    limit = 100

    while True:
        url = (
            _API
            + "?companyId=2022&limit="
            + str(limit)
            + "&offset="
            + str(offset)
        )
        payload = json.loads(ctx.fetch(url))
        page = payload.get("data") or []

        for record in page:
            try:
                job_id = int(record.get("id"))
            except (TypeError, ValueError):
                continue
            if job_id in open_listings:
                records[job_id] = record

        if len(page) < limit:
            break
        offset += limit

    employment_types = {
        "vacancy_type_effective": "Efetivo",
        "vacancy_type_temporary": "Temporário",
        "vacancy_type_apprentice": "Aprendiz",
        "vacancy_type_internship": "Estágio",
        "vacancy_type_trainee": "Trainee",
        "vacancy_type_outsource": "Terceiro",
        "vacancy_type_freelancer": "Freelancer",
        "vacancy_type_autonomous": "Autônomo",
        "vacancy_type_intermittent": "Intermitente",
    }

    for job_id in order:
        record = records.get(job_id)
        if not record:
            continue

        listing = open_listings[job_id]
        description = _description(record)
        if not description:
            continue

        canonical_url = SITE.rstrip("/") + "/jobs/" + str(job_id)
        result = {
            "url": canonical_url,
            "title": (
                _first(record, ("name", "title"))
                or listing.get("title")
            ),
            "company": "Itaú Unibanco",
            "location": _location(listing),
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
            "apply_url": canonical_url,
        }

        optional = {
            "employment_type": employment_types.get(listing.get("type")),
            "workplace_type": (
                (listing.get("workplace") or {}).get("workplaceType")
            ),
            "department": (
                listing.get("department")
                or _first(record, ("department", "departmentName"))
            ),
            "posted_at": _first(
                record,
                ("publishedAt", "publishedDate", "createdAt"),
            ),
            "expires_at": _first(
                record,
                (
                    "registerEndDate",
                    "applicationDeadline",
                    "expiresAt",
                    "expirationDate",
                ),
            ),
        }

        for key, value in optional.items():
            if value is not None and value != "":
                result[key] = value

        yield result
