
import json, re, html

SITE = "https://jobs.valorcapitalgroup.com/jobs"
EXTRA_HOSTS = []

MAX_JOBS = 6

def clean_text(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None

def first_text(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        vals = []
        for item in value:
            txt = first_text(item)
            if txt:
                vals.append(txt)
        return ", ".join(vals) if vals else None
    if isinstance(value, dict):
        if value.get("name"):
            return first_text(value.get("name"))
        address_bits = []
        address = value.get("address")
        if isinstance(address, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                bit = first_text(address.get(key))
                if bit:
                    address_bits.append(bit)
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            bit = first_text(value.get(key))
            if bit:
                address_bits.append(bit)
        if address_bits:
            return ", ".join(address_bits)
        if value.get("value"):
            return first_text(value.get("value"))
    return None

def iter_jsonld_objects(value):
    stack = value if isinstance(value, list) else [value]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            yield item
            graph = item.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)

def parse_jsonld(html_text):
    pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for match in re.finditer(pattern, html_text, re.S | re.I):
        raw = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for item in iter_jsonld_objects(data):
            typ = item.get("@type")
            if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                return item
    return None

def parse_sitemap_jobs(xml):
    jobs = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S | re.I):
        loc_match = re.search(r"<loc>(.*?)</loc>", block, re.S | re.I)
        if not loc_match:
            continue
        url = html.unescape(loc_match.group(1)).strip().split("#", 1)[0]
        if "/companies/" not in url or "/jobs/" not in url:
            continue
        lastmod_match = re.search(r"<lastmod>(.*?)</lastmod>", block, re.S | re.I)
        lastmod = html.unescape(lastmod_match.group(1)).strip() if lastmod_match else ""
        jobs.append((lastmod, url))
    jobs.sort(reverse=True)
    return [url for _, url in jobs]

def salary_from_jsonld(data):
    salary = data.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    currency = first_text(salary.get("currency"))
    value = salary.get("value")
    if isinstance(value, dict):
        min_value = first_text(value.get("minValue"))
        max_value = first_text(value.get("maxValue"))
        unit = first_text(value.get("unitText"))
        if min_value and max_value:
            parts = [min_value + "-" + max_value]
        elif min_value:
            parts = [min_value]
        elif max_value:
            parts = [max_value]
        else:
            parts = []
        if currency:
            parts.append(currency)
        if unit:
            parts.append(unit)
        return " ".join(parts) if parts else None
    value_text = first_text(value)
    if value_text and currency:
        return value_text + " " + currency
    return value_text

def crawl(ctx):
    xml = ctx.fetch("https://jobs.valorcapitalgroup.com/sitemap.xml")
    seen = set()
    yielded = 0

    for url in parse_sitemap_jobs(xml):
        if url in seen:
            continue
        seen.add(url)
        if yielded >= MAX_JOBS:
            break

        page = ctx.fetch(url)
        data = parse_jsonld(page)
        if not data:
            continue

        title = first_text(data.get("title"))
        description = clean_text(data.get("description"))
        org = data.get("hiringOrganization") or {}
        company = first_text(org.get("name") if isinstance(org, dict) else org)
        location = first_text(data.get("jobLocation")) or first_text(data.get("applicantLocationRequirements"))

        if not (title and company and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        employment_type = first_text(data.get("employmentType"))
        if employment_type:
            job["employment_type"] = employment_type

        posted_at = first_text(data.get("datePosted"))
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = first_text(data.get("validThrough"))
        if expires_at:
            job["expires_at"] = expires_at

        external_id = first_text(data.get("identifier"))
        if external_id:
            job["external_id"] = external_id

        salary = salary_from_jsonld(data)
        if salary:
            job["salary"] = salary

        yielded += 1
        yield job
