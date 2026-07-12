import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://www.comeet.co/careers-api/2.0/company/43.001/positions?token=34110453411D4968234168209C31D49"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _details_url(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["details"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


def crawl(ctx):
    rows = json.loads(ctx.fetch(_details_url(SITE)))

    groups = {}
    for row in rows:
        base_uid = row["uid"].split("-", 1)[0]
        group = groups.setdefault(base_uid, {"primary": row, "locations": []})
        location = (row.get("location") or {}).get("name")
        if location and location not in group["locations"]:
            group["locations"].append(location)
        if row.get("uid") == base_uid:
            group["primary"] = row

    for base_uid, group in groups.items():
        row = group["primary"]
        detail = row
        sections = sorted(detail.get("details") or [], key=lambda x: x.get("order", 0))
        body = []
        for section in sections:
            section_text = _text(section.get("value"))
            if section_text:
                name = _text(section.get("name"))
                body.append((name + "\n" if name else "") + section_text)
        description = "\n\n".join(body).strip()

        canonical_url = (detail.get("url_comeet_hosted_page") or
                         detail.get("url_active_page") or row["position_url"])
        fields = {
            "url": canonical_url,
            "source": SITE,
            "title": detail.get("name") or row.get("name"),
            "company": detail.get("company_name") or row.get("company_name") or "VAST Data",
            "location": (detail.get("location") or row.get("location") or {}).get("name"),
            "description": description,
            "external_id": base_uid,
            "apply_url": detail.get("url_active_page") or detail.get("url_comeet_hosted_page"),
            "department": detail.get("department") or row.get("department"),
            "employment_type": detail.get("employment_type") or row.get("employment_type"),
            "workplace_type": detail.get("workplace_type") or row.get("workplace_type"),
        }
        if len(group["locations"]) > 1:
            fields["locations"] = group["locations"]
        yield {key: value for key, value in fields.items()
               if value is not None and value != ""}
