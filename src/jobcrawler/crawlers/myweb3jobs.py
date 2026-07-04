import re, html, json

SITE = "https://myweb3jobs.com/wp-json/wp/v2/job"
EXTRA_HOSTS = []


def strip_html(s):
    s = html.unescape(s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(p|div|li|h[1-6]|ul|ol)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def parse_dl(content_html):
    meta = {}
    for m in re.finditer(
        r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", content_html, re.DOTALL | re.IGNORECASE
    ):
        key = strip_html(m.group(1)).lower()
        val = strip_html(m.group(2))
        if key and val:
            meta[key] = val
    return meta


def crawl(ctx):
    page = 1
    while page <= 10:
        url = (
            f"{SITE}?per_page=10&page={page}&_fields=id,link,date_gmt,title,content,acf"
        )
        try:
            resp = ctx.fetch(url)
        except Exception:
            break
        try:
            data = json.loads(resp)
        except Exception:
            break
        if not data:
            break
        for jd in data:
            acf = jd.get("acf") or {}
            content_html = jd.get("content", {}).get("rendered", "") or ""
            dl = parse_dl(content_html)
            title = html.unescape(jd.get("title", {}).get("rendered", "")).strip()
            company = (acf.get("company_name") or "").strip() or None
            location = (acf.get("location") or "").strip() or dl.get("location") or None
            description = strip_html(content_html)
            if not title or not description:
                continue
            remote = (acf.get("remote") or "").strip()
            workplace = (
                "Remote" if remote == "1" else (dl.get("workplace type") or None)
            )
            min_sal = (acf.get("minimum_salary") or "").strip()
            max_sal = (acf.get("maximum_salary") or "").strip()
            salary = None
            if min_sal and max_sal:
                salary = f"{min_sal} - {max_sal}"
            elif min_sal:
                salary = min_sal
            elif max_sal:
                salary = max_sal
            apply_url = (acf.get("apply_url_or_email") or "").strip()
            if apply_url and not apply_url.startswith("http"):
                apply_url = None
            yield {
                "url": jd.get("link", ""),
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": SITE,
                "salary": salary,
                "employment_type": (acf.get("employment_type") or "").strip()
                or dl.get("employment type")
                or None,
                "workplace_type": workplace,
                "department": dl.get("department") or None,
                "posted_at": (jd.get("date_gmt") or "").strip() or None,
                "expires_at": None,
                "external_id": str(jd["id"]) if jd.get("id") else None,
                "apply_url": apply_url or None,
            }
        if len(data) < 10:
            break
        page += 1
