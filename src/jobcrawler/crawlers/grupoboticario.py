import re, html, json
from urllib.parse import urlencode

SITE = "https://grupoboticario.gupy.io/"
EXTRA_HOSTS = ["employability-portal.gupy.io"]

COMPANY = "Grupo Boticário"
COMPANY_ID = 295
API_BASE = "https://employability-portal.gupy.io/api/v1/jobs"

TYPE_MAP = {
    "vacancy_type_effective": "CLT",
    "vacancy_type_legal_entity": "PJ",
    "vacancy_type_temporary": "temporary",
    "vacancy_type_talent_pool": "talent_pool",
}


def _clean_title(name):
    name = re.sub(r"\s*[\|｜].*$", "", name).strip()
    name = name.rstrip("·").strip()
    return name


def _clean_desc(desc):
    if not desc:
        return ""
    desc = html.unescape(desc)
    desc = re.sub(r"<[^>]+>", "", desc)
    desc = re.sub(r"\r\n|\r", "\n", desc)
    desc = re.sub(r"\n{3,}", "\n\n", desc)
    return desc.strip()


def crawl(ctx):
    offset = 0
    limit = 100
    seen = set()
    while True:
        qs = urlencode({"companyId": COMPANY_ID, "limit": limit, "offset": offset})
        url = f"{API_BASE}?{qs}"
        resp = ctx.fetch(url)
        data = json.loads(resp)
        jobs = data.get("data", [])
        if not jobs:
            break
        for j in jobs:
            jid = j.get("id")
            if jid in seen:
                continue
            seen.add(jid)

            title = _clean_title(j.get("name", ""))
            description = _clean_desc(j.get("description", ""))

            parts = []
            for f in ("city", "state", "country"):
                v = j.get(f)
                if v:
                    parts.append(str(v))
            location = ", ".join(parts) if parts else None

            posted = j.get("publishedDate")
            if posted:
                posted = posted.replace("Z", "+00:00")
            expires = j.get("applicationDeadline")

            etype = TYPE_MAP.get(j.get("type"))

            wpt = j.get("workplaceType")
            if not wpt and j.get("isRemoteWork"):
                wpt = "remote"

            job_url = j.get("jobUrl")
            if not job_url:
                job_url = f"https://grupoboticario.gupy.io/jobs/{jid}"

            yield {
                "url": job_url,
                "title": title or None,
                "company": COMPANY,
                "location": location,
                "description": description or None,
                "source": SITE,
                "external_id": str(jid) if jid else None,
                "posted_at": posted,
                "expires_at": expires if expires else None,
                "employment_type": etype,
                "workplace_type": wpt,
            }
        if len(jobs) < limit:
            break
        offset += limit
