import re
import html
from xml.etree import ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse

SITE = "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
EXTRA_HOSTS = []


def _strip_tags(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    # Unescape HTML entities first
    text = html.unescape(text)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(date_str):
    """Parse RFC 822 date string to ISO format."""
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
    """Crawl the WWR back-end programming RSS feed."""
    resp = ctx.fetch(SITE)
    root = ET.fromstring(resp)
    channel = root.find("channel")
    if channel is None:
        return
    for item in channel.findall("item"):
        title_raw = item.findtext("title") or ""
        # Title format: "Company: Role"
        if ": " in title_raw:
            company, job_title = title_raw.split(": ", 1)
        else:
            company = ""
            job_title = title_raw

        link = item.findtext("link") or ""
        guid = item.findtext("guid") or ""
        url = link or guid

        desc_html = item.findtext("description") or ""
        description = _strip_tags(desc_html)

        region = item.findtext("region") or ""
        location = region if region else None
        countries = _countries(item)
        if countries:
            location = "Remote (" + ", ".join(countries) + ")"

        job_type = item.findtext("type") or ""
        employment_type = job_type if job_type else None

        category = item.findtext("category") or ""
        department = category if category else None

        pub_date = item.findtext("pubDate") or ""
        posted_at = _parse_date(pub_date)

        expires_at_raw = item.findtext("expires_at") or ""
        expires_at = _parse_date(expires_at_raw)

        skills = item.findtext("skills") or ""

        external_id = None
        if guid:
            external_id = guid.rsplit("/", 1)[-1] if "/" in guid else guid

        job = {
            "url": url,
            "title": job_title.strip(),
            "company": company.strip(),
            "location": location,
            "description": description,
            "employment_type": employment_type,
            "department": department,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "external_id": external_id,
            "source": SITE,
        }
        if countries:
            job["locations"] = countries
        apply, salary = _detail(ctx, url)
        if apply:
            job["apply_url"] = apply
        if salary:
            job["salary"] = salary
        yield job
