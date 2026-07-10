import re, html
from xml.etree import ElementTree as ET
from datetime import datetime

SITE = "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"
EXTRA_HOSTS = []


def _strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|ul|ol)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%a, %d %b %Y %H:%M:%S %z")
        return dt.isoformat()
    except Exception:
        return None


def _countries(item):
    """Per-country tuple from the RSS ``<country>`` GeoLock, or () when unlocked.

    WWR's ``<region>`` is the employer-chosen listing bucket (near-always
    "Anywhere in the World" — it maximizes visibility), while the real hiring
    restriction is the GeoLock the detail page shows as country boxes. The feed
    mirrors that allowlist into ``<country>`` as flag-decorated English names:
    "🇨🇦 Canada and 🇺🇸 United States of America". Trusting the region alone made a
    US/Canada-only posting score as Brazil-eligible "Remote (Worldwide)", so when
    the allowlist is present it — not the region — is the location.

    Names are split on the flag emoji (a regional-indicator pair), NOT on the
    separators: the list mixes ", ", " and ", and ", and", and country names
    themselves contain " and " (Bosnia and Herzegovina).
    """
    raw = (item.findtext("country") or "").strip()
    if not raw:
        return ()
    out = []
    for part in re.split(r"[\U0001F1E6-\U0001F1FF]{2}", raw):
        name = re.sub(r"[\s,]+(?:and[\s,]*)?$", "", part.strip())
        if name:
            out.append(name)
    return tuple(out)


_SALARY_RE = re.compile(
    r'href="/remote-[^"]*-salary-jobs"[^>]*>\s*<span[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)


def _detail(ctx, page_url):
    """(apply_url, salary) read from the WWR listing page — one fetch serves both.

    The RSS feed never carries the real per-job apply link — only the WWR listing
    page and the company's homepage. The apply link is the "Apply now" button on the
    listing page itself: ``<a id="job-cta-alt" href="https://company-ats/...">``. So
    fetch the page and read that href. apply_url is None on any fetch/parse failure,
    when the button is absent, or when it points back at WWR (an in-house form, not a
    distinct target). The caller then omits ``apply_url`` entirely, so a transient
    miss never clobbers a value a previous crawl already stored.

    The same page's "About the job" sidebar prints a salary band behind WWR's
    salary-taxonomy link (``href="/remote-100k-or-more-salary-jobs"`` →
    "$100,000 or more USD") that the feed omits; it is returned verbatim for the
    Job.salary seam — parsing is normalize.parse_salary's job host-side. Same
    fail-open contract: None never clobbers a stored value.
    """
    if not page_url:
        return None, None
    try:
        page = ctx.fetch(page_url)
    except Exception:
        return None, None
    apply_url = None
    tag = re.search(r'<a\b[^>]*\bid=["\']job-cta-alt["\'][^>]*>', page, re.IGNORECASE)
    if tag:
        href = re.search(r'\bhref=["\']([^"\']+)["\']', tag.group(0), re.IGNORECASE)
        if href:
            url = html.unescape(href.group(1)).strip()
            if url.lower().startswith(("http://", "https://")) and (
                "weworkremotely.com" not in url.lower()
            ):
                apply_url = url
    salary = None
    m = _SALARY_RE.search(page)
    if m:
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        salary = text or None
    return apply_url, salary


def crawl(ctx):
    raw = ctx.fetch(SITE)
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        return
    items = channel.findall("item")
    seen_links = set()
    for item in items:
        link = item.findtext("link", "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        title_raw = item.findtext("title", "").strip()
        if ": " in title_raw:
            company, role = title_raw.split(": ", 1)
            company = company.strip()
            role = role.strip()
        else:
            company = ""
            role = title_raw

        desc_html = item.findtext("description", "")
        description = _strip_html(desc_html)

        region = item.findtext("region", "").strip() or None
        job_type = item.findtext("type", "").strip() or None
        category = item.findtext("category", "").strip() or None

        posted_at = _parse_date(item.findtext("pubDate", ""))
        expires_at = _parse_date(item.findtext("expires_at", ""))

        guid = item.findtext("guid", "").strip() or None

        job = {
            "url": link,
            "title": role,
            "company": company,
            "location": region,
            "description": description,
            "source": SITE,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "employment_type": job_type,
            "department": category,
            "external_id": guid,
        }
        countries = _countries(item)
        if countries:
            job["location"] = "Remote (" + ", ".join(countries) + ")"
            job["locations"] = countries
        apply, salary = _detail(ctx, link)
        if apply:
            job["apply_url"] = apply
        if salary:
            job["salary"] = salary
        yield job
