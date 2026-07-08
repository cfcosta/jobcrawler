
import re
import json
import html
from urllib.parse import urljoin

SITE = "https://www.hacker-jobs.com/"
EXTRA_HOSTS = []

def _split_args(s):
    args = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        if s[i] == '"':
            j = i + 1
            esc = False
            while j < n:
                ch = s[j]
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    j += 1
                    break
                j += 1
            args.append(json.loads(s[i:j]))
            i = j
        else:
            j = i
            depth = 0
            while j < n:
                ch = s[j]
                if ch == "," and depth == 0:
                    break
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                j += 1
            raw = s[i:j].strip()
            if raw == "null":
                args.append(None)
            elif raw == "true":
                args.append(True)
            elif raw == "false":
                args.append(False)
            elif raw:
                try:
                    args.append(int(raw))
                except ValueError:
                    try:
                        args.append(float(raw))
                    except ValueError:
                        args.append(raw)
            i = j + 1
    return args

def _alias_map(page):
    m = re.search(r"\(function\(([^)]*)\)\{return \{state:\{\$sjobsData:", page)
    tail = re.search(r"\}\}\((.*?)\)\)\}</script>", page, re.S)
    if not m or not tail:
        return {}
    params = [p.strip() for p in m.group(1).split(",")]
    return dict(zip(params, _split_args(tail.group(1))))

def _resolve(token, aliases):
    token = token.strip()
    if not token:
        return None
    if token.startswith('"'):
        return json.loads(token)
    if token in aliases:
        return aliases[token]
    if token == "null":
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    try:
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return None

def _field(obj, key, aliases):
    m = re.search(r"\b" + re.escape(key) + r':((?:"(?:\\.|[^"\\])*")|-?\d+(?:\.\d+)?|[A-Za-z_$][\w$]*)', obj)
    if not m:
        return None
    return _resolve(m.group(1), aliases)

def _job_objects(page):
    start = page.find("$sjobsData:")
    if start < 0:
        return []
    end = page.find(",$sfavoriteIds", start)
    if end < 0:
        end = page.find("},_errors", start)
    data = page[start:end]
    return re.findall(r"\{id:(?:(?!\},\{id:|\}\],|em:).)*?em:(?:[A-Za-z_$][\w$]*|\[[^\]]*\])\}", data, re.S)

def _clean_text(*parts):
    text = "\n\n".join(str(p).strip() for p in parts if p not in (None, ""))
    text = html.unescape(text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip() or None

def crawl(ctx):
    page = ctx.fetch(SITE)
    aliases = _alias_map(page)
    seen = set()

    for obj in _job_objects(page):
        jid = _field(obj, "jid", aliases)
        hn_id = _field(obj, "id", aliases)
        title = _field(obj, "jt", aliases)
        company = _field(obj, "cn", aliases)
        location = _field(obj, "cl", aliases) or _field(obj, "clt", aliases)
        company_desc = _field(obj, "cd", aliases)
        requirement = _field(obj, "req", aliases)
        salary = _field(obj, "sa", aliases)
        posted_at = _field(obj, "at", aliases)
        issue = _field(obj, "is", aliases)
        remote = _field(obj, "re", aliases)
        part_time = _field(obj, "pt", aliases)

        if not (jid and title and company):
            continue
        if str(jid) in seen:
            continue
        seen.add(str(jid))

        desc_bits = []
        if company_desc:
            desc_bits.append(company_desc)
        desc_bits.append("Role: %s at %s" % (title, company))
        if requirement:
            desc_bits.append("Requirements: " + str(requirement))
        if salary:
            desc_bits.append("Compensation: " + str(salary))
        if issue:
            desc_bits.append("Hacker News Who is hiring issue: " + str(issue))
        description = _clean_text(*desc_bits)
        if not description:
            continue

        if not location and remote is True:
            location = "Remote"
        if not location:
            location = "Unspecified"

        job = {
            "url": SITE + "#" + str(jid),
            "title": str(title).strip(),
            "company": str(company).strip(),
            "location": str(location).strip(),
            "description": description,
            "source": SITE,
            "external_id": str(jid),
        }
        if hn_id:
            job["apply_url"] = "https://news.ycombinator.com/item?id=" + str(hn_id)
        if salary:
            job["salary"] = str(salary).strip()
        if posted_at:
            job["posted_at"] = str(posted_at).strip()
        if remote is True:
            job["workplace_type"] = "remote"
        elif remote is False:
            job["workplace_type"] = "onsite"
        if part_time is True:
            job["employment_type"] = "part-time"
        yield job
