import html
import re
from urllib.parse import urljoin

SITE = "https://aijobs.net/"
EXTRA_HOSTS = []

MAX_DETAILS = 4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RE_JOB_LINK = re.compile(r'href="(/job/[a-z0-9-]+-\d+/)"', re.I)
RE_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
RE_H5 = re.compile(r"<h5\b", re.I)
RE_STRONG = re.compile(r"<strong[^>]*>(.*?)</strong>", re.I | re.S)
RE_COMPANY = re.compile(r'href="/company/[^"]*"[^>]*>(.*?)</a>', re.I | re.S)
RE_PAY = re.compile(r'<p class="py-4">(.*?)</p>', re.I | re.S)
RE_SUCCESS = re.compile(r"text-bg-success[^>]*>(.*?)</span>", re.I | re.S)
RE_SECONDARY = re.compile(r"text-bg-secondary[^>]*>(.*?)</span>", re.I | re.S)
RE_ID = re.compile(r"-(\d+)/?$")
RE_END = re.compile(r'<div[^>]*class="row g-2"', re.I)
RE_RELATED = re.compile(r"Related jobs", re.I)
RE_MAIN_END = re.compile(r"</main>", re.I)
RE_AT = re.compile(r"^@\s*")
RE_REMOTE = re.compile(r"remote", re.I)

RE_TAG = re.compile(r"<[^>]+>")
RE_BR = re.compile(r"(?i)<br\s*/?>")
RE_BLOCK = re.compile(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|tr|section)>")
RE_LI = re.compile(r"(?i)<li[^>]*>")
RE_PARTIAL = re.compile(r"<[^>]*$")
RE_WS = re.compile(r"[ \t\r\f\v]+")
RE_NL = re.compile(r" *\n *")
RE_NL3 = re.compile(r"\n{3,}")


def _inline(value):
    if not value:
        return None
    return RE_TAG.sub("", html.unescape(value)).strip() or None


def _body_text(fragment):
    text = RE_BR.sub("\n", fragment)
    text = RE_BLOCK.sub("\n", text)
    text = RE_LI.sub("- ", text)
    text = RE_TAG.sub(" ", text)
    text = html.unescape(text)
    text = RE_WS.sub(" ", text)
    text = RE_NL.sub("\n", text)
    text = RE_NL3.sub("\n\n", text)
    return text.strip() or None


def _clean(data):
    return {k: v for k, v in data.items() if v not in (None, "")}


def _parse(page, url):
    title_match = RE_H1.search(page)
    if not title_match:
        return None
    title = _inline(title_match.group(1))
    tail = page[title_match.start() :]
    h5_match = RE_H5.search(tail)
    head = tail[: h5_match.start()] if h5_match else tail

    loc_match = RE_STRONG.search(head)
    location = _inline(loc_match.group(1)) if loc_match else None

    company = None
    comp_match = RE_COMPANY.search(head)
    if comp_match:
        company = _inline(comp_match.group(1))
        if company:
            company = RE_AT.sub("", company).strip() or None

    salary = None
    employment = None
    pay_match = RE_PAY.search(head)
    if pay_match:
        for value in RE_SUCCESS.findall(pay_match.group(1)):
            value = _inline(value)
            if value and value != "R":
                salary = value
                break
        emp_match = RE_SECONDARY.search(pay_match.group(1))
        if emp_match:
            employment = _inline(emp_match.group(1))

    description = None
    if h5_match:
        rest = tail[h5_match.start() :]
        end = RE_END.search(rest) or RE_RELATED.search(rest) or RE_MAIN_END.search(rest)
        body = rest[: end.start()] if end else rest
        body = RE_PARTIAL.sub("", body)
        description = _body_text(body)

    if not (title and company and location and description):
        return None

    out = {
        "source": "aijobs",
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "salary": salary,
        "employment_type": employment,
    }
    if RE_REMOTE.search(location):
        out["workplace_type"] = "Remote"
    id_match = RE_ID.search(url)
    if id_match:
        out["external_id"] = id_match.group(1)
    return _clean(out)


def _fetch(ctx, url):
    last = None
    for _ in range(2):
        try:
            return ctx.fetch(url, headers=HEADERS)
        except Exception as exc:
            last = exc
    raise last


def crawl(ctx):
    listing = _fetch(ctx, SITE + "?page=1")
    seen = set()
    urls = []
    for match in RE_JOB_LINK.finditer(listing):
        url = urljoin(SITE, match.group(1))
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    for url in urls[:MAX_DETAILS]:
        try:
            page = _fetch(ctx, url)
        except Exception:
            continue
        job = _parse(page, url)
        if job:
            yield job
