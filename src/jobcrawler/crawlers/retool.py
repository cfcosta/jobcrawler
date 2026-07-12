
import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

SITE = "https://retool.com/careers"
EXTRA_HOSTS = []

SITEMAP_URL = "https://retool.com/sitemap.xml"


def plain_text(fragment):
    fragment = re.sub(r"<\s*br\s*/?\s*>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<\s*li\b[^>]*>", "\n- ", fragment, flags=re.I)
    fragment = re.sub(
        r"</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|"
        r"pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(
        r"<script\b[^>]*>.*?</script\s*>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"<style\b[^>]*>.*?</style\s*>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(fragment).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sitemap_job_urls(xml_text):
    try:
        root = ElementTree.fromstring(xml_text)
        candidates = [
            (node.text or "").strip()
            for node in root.iter()
            if node.tag.rsplit("}", 1)[-1] == "loc"
        ]
    except ElementTree.ParseError:
        candidates = re.findall(
            r"<loc>\s*([^<]+?)\s*</loc>",
            xml_text,
            flags=re.I,
        )

    urls = []
    seen = set()
    for candidate in candidates:
        parsed = urlparse(html.unescape(candidate))
        path = parsed.path.rstrip("/")
        if (
            parsed.scheme == "https"
            and parsed.netloc == "retool.com"
            and path.startswith("/careers/")
            and path.count("/") == 2
        ):
            url = "https://retool.com" + path
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def flight_chunks(document):
    chunks = []
    pattern = re.compile(
        r"<script>\s*self\.__next_f\.push\((\[.*?\])\)\s*</script>",
        flags=re.S,
    )
    for match in pattern.finditer(document):
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        if len(value) > 1 and isinstance(value[1], str):
            chunks.append(value[1])
    return chunks


def embedded_jobs(chunks):
    decoded = "".join(chunks)
    pattern = re.compile(
        r'\{"id":"([^"]+)","location":"([^"]+)","title":"([^"]+)",'
        r'"department":"([^"]+)","link":"([^"]+)","content":"\$([^"]+)",'
        r'"updatedAt":"([^"]+)"\}'
    )
    return [
        {
            "id": match.group(1),
            "location": match.group(2).strip(),
            "title": match.group(3).strip(),
            "department": match.group(4).strip(),
            "apply_url": match.group(5),
            "content_ref": match.group(6),
        }
        for match in pattern.finditer(decoded)
    ]


def content_records(chunks):
    records = {}
    for index in range(len(chunks) - 1):
        header = re.fullmatch(
            r"([0-9a-f]+):T([0-9a-f]+),",
            chunks[index].strip(),
        )
        if header:
            records[header.group(1)] = chunks[index + 1]
    return records


def words(value):
    value = html.unescape(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", value))


def url_parts(url):
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    pieces = slug.split("--")
    while len(pieces) < 3:
        pieces.append("")
    return pieces[0], pieces[1], pieces[2]


def similarity(left, right):
    left_words = words(left)
    right_words = words(right)
    if not left_words or not right_words:
        return 0.0
    common = len(left_words & right_words)
    return (2.0 * common) / (len(left_words) + len(right_words))


def canonical_url(job, available_urls):
    best_url = None
    best_score = -1.0

    for url in available_urls:
        title_part, department_part, location_part = url_parts(url)
        score = (
            10.0 * similarity(job["title"], title_part)
            + 2.0 * similarity(job["department"], department_part)
            + similarity(job["location"], location_part)
        )
        if score > best_score:
            best_score = score
            best_url = url

    return best_url


def salary_fields(description):
    match = re.search(
        r"(?:The\s+)?base pay range for this role is\s+"
        r"(\$[\d,]+)\s*(?:-|–|—|to)\s*(\$[\d,]+)\s+per\s+(year|hour)",
        description,
        flags=re.I,
    )
    if not match:
        return {}

    minimum = float(match.group(1).replace("$", "").replace(",", ""))
    maximum = float(match.group(2).replace("$", "").replace(",", ""))
    period_word = match.group(3)
    period = "yearly" if period_word.lower() == "year" else "hourly"

    return {
        "salary": description[match.start(1):match.end(3)],
        "salary_min": minimum,
        "salary_max": maximum,
        "salary_currency": "USD",
        "salary_period": period,
    }


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    careers_page = ctx.fetch(SITE)

    available_urls = sitemap_job_urls(sitemap)
    chunks = flight_chunks(careers_page)
    jobs = embedded_jobs(chunks)
    descriptions = content_records(chunks)

    for embedded in jobs:
        description_html = descriptions.get(embedded["content_ref"])
        if not description_html:
            continue

        description = plain_text(description_html)
        url = canonical_url(embedded, available_urls)
        if not url or not description:
            continue

        available_urls.remove(url)

        job = {
            "url": url,
            "title": embedded["title"],
            "company": "Retool",
            "location": embedded["location"],
            "description": description,
            "source": SITE,
            "external_id": embedded["id"],
            "apply_url": embedded["apply_url"],
        }

        if embedded["department"]:
            job["department"] = embedded["department"]

        job.update(salary_fields(description))
        yield job
