
import json
import re
from datetime import datetime, timezone

SITE = "https://4dayweek.io/software-engineering-jobs"
EXTRA_HOSTS = []


def _fetch(ctx, url):
    try:
        return ctx.fetch(url)
    except TypeError:
        return ctx.fetch(url, "GET", "")


def _push_strings(html):
    marker = "self.__next_f.push("
    chunks = []
    pos = 0
    while True:
        start = html.find(marker, pos)
        if start < 0:
            break
        i = start + len(marker)
        depth = 0
        in_str = False
        esc = False
        end = None
        for j in range(i, len(html)):
            c = html[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
        if end is None:
            break
        try:
            arr = json.loads(html[i:end])
            if isinstance(arr, list) and len(arr) > 1 and isinstance(arr[1], str):
                chunks.append(arr[1])
        except Exception:
            pass
        pos = end
    return chunks


def _matching_brace(s, start, max_len=50000):
    depth = 0
    in_str = False
    esc = False
    for i in range(start, min(len(s), start + max_len)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
    return None


def _extract_jobs(html):
    flight = "".join(_push_strings(html))
    jobs = []
    seen = set()
    for m in re.finditer(r'"company_name"\s*:', flight):
        p = m.start()
        starts = []
        for i in range(p, max(-1, p - 8000), -1):
            if flight[i] == "{":
                starts.append(i)
                if len(starts) >= 80:
                    break
        for start in starts:
            end = _matching_brace(flight, start)
            if not end:
                continue
            try:
                obj = json.loads(flight[start:end])
            except Exception:
                continue
            if isinstance(obj, dict) and {"id", "title", "slug", "company_name"} <= set(obj):
                key = obj.get("id") or obj.get("slug")
                if key not in seen:
                    seen.add(key)
                    jobs.append(obj)
                break
    return jobs


def _location_text(loc):
    if not isinstance(loc, dict):
        return str(loc)
    parts = []
    for key in ("city", "state", "country", "continent"):
        value = loc.get(key)
        if value and value not in parts:
            parts.append(str(value))
    return ", ".join(parts)


def _posted_at(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).date().isoformat()
    except Exception:
        return None


def _description(job, location, locations):
    lines = [
        job.get("title"),
        "Company: " + job.get("company_name", ""),
        "Location: " + location,
    ]
    if job.get("work_arrangement"):
        lines.append("Work arrangement: " + str(job["work_arrangement"]))
    if job.get("schedule_type"):
        lines.append("Schedule type: " + str(job["schedule_type"]).replace("_", " "))
    if job.get("category"):
        lines.append("Category: " + str(job["category"]))
    if job.get("level"):
        lines.append("Level: " + str(job["level"]))
    stack = [x.get("name") for x in job.get("stack") or [] if isinstance(x, dict) and x.get("name")]
    if stack:
        lines.append("Stack: " + ", ".join(stack))
    if locations:
        lines.append("All listed locations: " + "; ".join(locations))
    if job.get("salary_lower") or job.get("salary_upper"):
        low = job.get("salary_lower")
        high = job.get("salary_upper")
        cur = job.get("salary_currency") or ""
        per = job.get("salary_period") or ""
        def money(v):
            try:
                return str(int(v) / 100).rstrip("0").rstrip(".")
            except Exception:
                return str(v)
        if low and high:
            lines.append(f"Salary: {cur} {money(low)}-{money(high)} per {per}".strip())
        elif low:
            lines.append(f"Salary minimum: {cur} {money(low)} per {per}".strip())
        elif high:
            lines.append(f"Salary maximum: {cur} {money(high)} per {per}".strip())
    if job.get("posted"):
        lines.append("Posted: " + str(job.get("posted")))
    return "\n".join(x for x in lines if x)


def _job_kwargs(job):
    locs = [_location_text(x) for x in (job.get("locations") or [])]
    locs = [x for x in locs if x]
    primary = None
    for loc in job.get("locations") or []:
        if isinstance(loc, dict) and loc.get("is_primary"):
            primary = _location_text(loc)
            break
    location = primary or (locs[0] if locs else "Remote")
    url = "https://4dayweek.io/job/" + str(job["slug"])
    data = {
        "url": url,
        "title": job.get("title"),
        "company": job.get("company_name"),
        "location": location,
        "description": _description(job, location, locs),
        "external_id": job.get("id"),
        "source": SITE,
    }
    if locs:
        data["locations"] = locs
    if job.get("work_arrangement"):
        data["workplace_type"] = job.get("work_arrangement")
    if job.get("category"):
        data["department"] = job.get("category")
    posted = _posted_at(job.get("posted"))
    if posted:
        data["posted_at"] = posted
    if job.get("salary_currency"):
        data["salary_currency"] = job.get("salary_currency")
    if job.get("salary_period"):
        data["salary_period"] = job.get("salary_period")
    if job.get("salary_lower") is not None:
        try:
            data["salary_min"] = int(job.get("salary_lower")) / 100
        except Exception:
            pass
    if job.get("salary_upper") is not None:
        try:
            data["salary_max"] = int(job.get("salary_upper")) / 100
        except Exception:
            pass
    return {k: v for k, v in data.items() if v is not None and v != ""}


def crawl(ctx):
    seen = set()
    page = 1
    while True:
        url = SITE if page == 1 else SITE + "?page=" + str(page)
        try:
            html = _fetch(ctx, url)
        except Exception:
            break
        jobs = _extract_jobs(html)
        if not jobs:
            break
        for job in jobs:
            if job.get("is_expired"):
                continue
            key = job.get("id") or job.get("slug")
            if key in seen:
                continue
            seen.add(key)
            yield _job_kwargs(job)
        if len(jobs) < 50:
            break
        page += 1
