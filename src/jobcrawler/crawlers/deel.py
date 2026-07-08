
import json
import re
import html

SITE = "https://www.deel.com/careers/"
EXTRA_HOSTS = []
COMPANY = "Deel"


def clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def strip_html(value):
    if not value:
        return None

    # RSC text chunks can include split-script framing before the actual HTML payload.
    marker = 'self.__next_f.push([1,"'
    idx = value.rfind(marker)
    if idx != -1:
        value = value[idx + len(marker):]
    if value.startswith('"])'):
        value = value[3:]

    value = value.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    value = value.replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/")
    value = html.unescape(value)

    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    value = value.strip(' "\n\t')
    return value or None


def decode_record(raw):
    text = raw.replace('\\"', '"').replace("\\\\/", "/")
    text = text.replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&")
    try:
        return json.loads(text)
    except Exception:
        return None


def text_chunks(page):
    chunks = {}
    for match in re.finditer(r'([0-9a-f]+):T([0-9a-f]+),', page):
        key = match.group(1)
        length = int(match.group(2), 16)
        chunks[key] = page[match.end():match.end() + length]
    return chunks


def record_iter(page):
    pattern = r'\{\\"id\\":0,\\"attributes\\":\{.*?\\"slug\\":\\"/careers/position/\?ashby_jid=[^"]+\\"\}\}'
    for raw in re.findall(pattern, page):
        record = decode_record(raw)
        if record and isinstance(record.get("attributes"), dict):
            yield record["attributes"]


def location_text(attrs):
    values = []
    for loc in attrs.get("all_locations") or []:
        loc = clean(loc)
        if loc and loc not in values:
            values.append(loc)

    if not values:
        for item in attrs.get("locations_list") or []:
            if isinstance(item, dict):
                loc = clean(item.get("location"))
            else:
                loc = clean(item)
            if loc and loc not in values:
                values.append(loc)

    if values:
        return "; ".join(values)

    return clean(attrs.get("location_name")) or "Remote"


def truthy(value):
    return value is True or value == "true" or value == "True" or value == 1


def crawl(ctx):
    page = ctx.fetch(SITE)
    chunks = text_chunks(page)
    seen = set()

    for attrs in record_iter(page):
        if attrs.get("is_listed") is False:
            continue

        title = clean(attrs.get("title"))
        slug = clean(attrs.get("slug"))
        if not title or not slug:
            continue

        url = "https://www.deel.com" + slug if slug.startswith("/") else slug
        if url in seen:
            continue
        seen.add(url)

        desc_ref = clean(attrs.get("full_job_description"))
        raw_desc = None
        if desc_ref and desc_ref.startswith("$"):
            raw_desc = chunks.get(desc_ref[1:])
        elif desc_ref:
            raw_desc = desc_ref
        description = strip_html(raw_desc)
        if not description:
            seo = attrs.get("SEO") if isinstance(attrs.get("SEO"), dict) else {}
            description = clean(seo.get("description"))
        if not description:
            continue

        item = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location_text(attrs),
            "description": description,
            "source": SITE,
        }

        employment_type = clean(attrs.get("employment_type"))
        if employment_type:
            item["employment_type"] = employment_type

        department = clean(attrs.get("department_name") or attrs.get("team_name") or attrs.get("tier_2_category"))
        if department:
            item["department"] = department

        posted_at = clean(attrs.get("ashby_published_date"))
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = clean(attrs.get("application_deadline"))
        if expires_at:
            item["expires_at"] = expires_at

        external_id = clean(attrs.get("ashby_id") or attrs.get("job_id"))
        if external_id:
            item["external_id"] = external_id

        apply_url = clean(attrs.get("external_link"))
        if apply_url:
            item["apply_url"] = apply_url

        salary = clean(attrs.get("compensation_tier_summary"))
        if salary and truthy(attrs.get("should_display_compensation_on_Job_board")):
            item["salary"] = salary

        yield item
