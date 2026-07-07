import html
import json
import re

SITE = "https://block.xyz/careers/jobs"
EXTRA_HOSTS = []

MAX_POSTINGS = 8

_STR = r'"(?:[^"\\]|\\.)*"'

_LIST_ID = re.compile(r"\{id:(\d+),internalId:\d+,requisitionId:" + _STR)

_JOB = re.compile(
    r"job:\{id:(?P<id>\d+),internalId:\d+,requisitionId:"
    + _STR
    + r",title:(?P<title>"
    + _STR
    + r")"
    + r",content:(?P<content>"
    + _STR
    + r")"
    + r"(?:,bu:"
    + _STR
    + r")?"
    + r",employeeType:(?P<emp>"
    + _STR
    + r")"
    + r",jobFunction:(?P<func>"
    + _STR
    + r")"
    + r",isRemote:(?P<remote>true|false)"
    + r",location:(?P<loc>"
    + _STR
    + r")"
    + r",publicationDate:(?P<pub>null|"
    + _STR
    + r")"
)

_BR = re.compile(r"<\s*br\s*/?>", re.I)
_BLOCK = re.compile(r"</\s*(p|div|li|h[1-6]|tr|ul|ol|table|section)\s*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_LINE = re.compile(r" *\n *")
_GAP = re.compile(r"\n{3,}")

_FALLBACK = (('\\"', '"'), ("\\/", "/"), ("\\n", "\n"), ("\\t", "\t"), ("\\r", ""))


def _js(literal):
    if not literal or literal == "null":
        return None
    try:
        value = json.loads(literal)
    except Exception:
        value = literal[1:-1]
        for token, repl in _FALLBACK:
            value = value.replace(token, repl)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _plain(literal):
    text = _js(literal)
    if not text:
        return None
    text = html.unescape(text)
    text = _BR.sub("\n", text)
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _SPACE.sub(" ", text)
    text = _LINE.sub("\n", text)
    text = _GAP.sub("\n\n", text).strip()
    return text or None


def _clean(data):
    return {k: v for k, v in data.items() if v not in (None, "")}


def crawl(ctx):
    listing = ctx.fetch(SITE)
    ids = []
    seen = set()
    for match in _LIST_ID.finditer(listing):
        jid = match.group(1)
        if jid not in seen:
            seen.add(jid)
            ids.append(jid)

    for jid in ids[:MAX_POSTINGS]:
        url = f"https://block.xyz/careers/jobs/{jid}"
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        job = _JOB.search(page)
        if not job:
            continue
        title = _js(job.group("title"))
        description = _plain(job.group("content"))
        location = _js(job.group("loc"))
        if not (title and description and location):
            continue
        out = {
            "url": url,
            "title": title,
            "company": "Block",
            "location": location,
            "description": description,
            "source": "block",
            "external_id": jid,
            "department": _js(job.group("func")),
            "employment_type": _js(job.group("emp")),
            "posted_at": _js(job.group("pub")),
        }
        if job.group("remote") == "true":
            out["workplace_type"] = "Remote"
        yield _clean(out)
