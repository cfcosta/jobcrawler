
import html
import json
import re

Job = dict

SITE = "https://asaas.gupy.io/"
EXTRA_HOSTS = []


def _plain_text(fragment):
    if not fragment:
        return None

    value = re.sub(
        r"(?i)<li(?:\s[^>]*)?>",
        "\n- ",
        fragment,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|main|"
        r"nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _next_data(document):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Page does not contain Gupy __NEXT_DATA__")
    return json.loads(html.unescape(match.group(1)))


def _location(job):
    line = job.get("addressLine")
    if line:
        return line

    parts = []
    for key in ("addressCity", "addressState", "addressCountry"):
        value = job.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else "Brasil"


def _description(job):
    sections = (
        ("Descrição da vaga", "description"),
        ("Responsabilidades e atribuições", "responsibilities"),
        ("Requisitos e qualificações", "prerequisites"),
        ("Diferenciais", "relevantExperiences"),
        ("Informações adicionais", "additionalInformation"),
    )
    output = []
    for heading, key in sections:
        text = _plain_text(job.get(key))
        if text:
            output.append(f"{heading}\n{text}")
    return "\n\n".join(output)


def _employment_type(value):
    labels = {
        "vacancy_type_effective": "Efetivo",
        "vacancy_type_temporary": "Temporário",
        "vacancy_type_internship": "Estágio",
        "vacancy_type_apprentice": "Aprendiz",
        "vacancy_type_autonomous": "Autônomo",
        "vacancy_type_outsourced": "Terceirizado",
    }
    return labels.get(value, value) if value else None


def crawl(ctx):
    homepage = _next_data(ctx.fetch(SITE))
    page_props = homepage["props"]["pageProps"]

    for listing in page_props.get("jobs", []):
        job_id = listing.get("id")
        if job_id is None:
            continue

        url = f"{SITE}jobs/{job_id}"
        detail = _next_data(ctx.fetch(url))
        job = detail["props"]["pageProps"]["job"]

        if job.get("status") and job["status"] != "published":
            continue

        description = _description(job)
        if not description:
            continue

        yield Job(
            url=url,
            source=SITE,
            title=job.get("name") or listing.get("title"),
            company="Asaas",
            location=_location(job),
            description=description,
            department=listing.get("department"),
            employment_type=_employment_type(
                job.get("jobType") or listing.get("type")
            ),
            workplace_type=(
                job.get("workplaceType")
                or (listing.get("workplace") or {}).get("workplaceType")
            ),
            posted_at=job.get("publishedAt"),
            expires_at=job.get("expiresAt"),
            external_id=str(job_id),
            apply_url=url,
        )
