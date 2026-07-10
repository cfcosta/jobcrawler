SITE = "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss"
EXTRA_HOSTS = []

import re, html
import xml.etree.ElementTree as ET
from datetime import datetime


def _strip_tags(html_str):
    """Remove HTML tags, unescape entities, and clean whitespace."""
    if not html_str:
        return ""
    text = html.unescape(html_str)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_date(date_str):
    """Parse RSS date format like 'Mon, 22 Jun 2026 15:29:54 +0000'."""
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
    the allowlist is present it — not the region — is the location. The feed's
    ``<state>`` is deliberately ignored: it is an employer-address artifact
    ("Alabama", "Delaware", even "London") on fully-remote listings, and joining
    it into the location (as this crawler once did) invents a US-state signal
    the posting never made.

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


def _text_or_none(elem):
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


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
    items = root.findall(".//item")

    for item in items:
        full_title = _text_or_none(item.find("title")) or ""
        if ": " in full_title:
            company, role = full_title.split(": ", 1)
        else:
            company, role = "", full_title

        url = _text_or_none(item.find("link")) or ""
        if not url:
            continue

        desc_html = _text_or_none(item.find("description")) or ""
        description = _strip_tags(desc_html)
        if not description:
            continue

        region = _text_or_none(item.find("region")) or ""
        countries = _countries(item)
        if countries:
            location = "Remote (" + ", ".join(countries) + ")"
        else:
            location = region or None

        employment_type = _text_or_none(item.find("type"))
        posted_at = _parse_date(_text_or_none(item.find("pubDate")))
        expires_at = _parse_date(_text_or_none(item.find("expires_at")))

        guid = _text_or_none(item.find("guid"))

        job = {
            "url": url,
            "title": role.strip(),
            "company": company.strip(),
            "location": location,
            "description": description,
            "source": SITE,
        }
        if employment_type:
            job["employment_type"] = employment_type
        if posted_at:
            job["posted_at"] = posted_at
        if expires_at:
            job["expires_at"] = expires_at
        if guid:
            job["external_id"] = guid
        if countries:
            job["locations"] = countries

        apply, salary = _detail(ctx, url)
        if apply:
            job["apply_url"] = apply
        if salary:
            job["salary"] = salary

        yield job
