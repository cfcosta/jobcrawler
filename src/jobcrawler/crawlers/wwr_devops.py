SITE = "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
EXTRA_HOSTS = []

import xml.etree.ElementTree as ET
import html
import re
from datetime import datetime


def _strip_tags(text):
    """Unescape HTML entities and strip tags."""
    unescaped = html.unescape(text)
    # Replace <br>, <p>, </p>, </li>, </h1-6> with newlines for readability
    unescaped = re.sub(r"(?i)<br\s*/?>", "\n", unescaped)
    unescaped = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", unescaped)
    unescaped = re.sub(r"(?i)<li[^>]*>", "- ", unescaped)
    clean = re.sub(r"<[^>]+>", "", unescaped)
    # Collapse excessive whitespace
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    return clean.strip()


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
    rss_text = ctx.fetch(SITE)
    root = ET.fromstring(rss_text)
    channel = root.find("channel")
    if channel is None:
        return
    items = channel.findall("item")

    for item in items:
        # Extract fields
        title_raw = ""
        link = ""
        description_raw = ""
        pub_date = ""
        expires_at = ""
        region = ""
        job_type = ""
        category = ""
        guid = ""

        for child in item:
            tag = child.tag.split("}")[-1]
            text = (child.text or "").strip()
            if tag == "title":
                title_raw = text
            elif tag == "link":
                link = text
            elif tag == "description":
                description_raw = text
            elif tag == "pubDate":
                pub_date = text
            elif tag == "expires_at":
                expires_at = text
            elif tag == "region":
                region = text
            elif tag == "type":
                job_type = text
            elif tag == "category":
                category = text
            elif tag == "guid":
                guid = text

        # Split "Company: Role"
        if ": " in title_raw:
            company, title = title_raw.split(": ", 1)
        else:
            company = ""
            title = title_raw

        # Full description
        description = _strip_tags(description_raw)

        job = {
            "url": link,
            "title": title,
            "company": company,
            "location": region if region else "Remote",
            "description": description,
            "source": SITE,
        }
        countries = _countries(item)
        if countries:
            job["location"] = "Remote (" + ", ".join(countries) + ")"
            job["locations"] = countries

        if job_type:
            job["employment_type"] = job_type
        if category:
            job["department"] = category
        if pub_date:
            parsed = _parse_date(pub_date)
            if parsed:
                job["posted_at"] = parsed
        if expires_at:
            parsed = _parse_date(expires_at)
            if parsed:
                job["expires_at"] = parsed
        if guid:
            job["external_id"] = guid

        apply, salary = _detail(ctx, link)
        if apply:
            job["apply_url"] = apply
        if salary:
            job["salary"] = salary

        yield job
