
import html
import re

SITE = "https://www.charliehr.com/careers"
EXTRA_HOSTS = []


def _text(value):
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|section|li|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    page = ctx.fetch(SITE)

    vacancy_match = re.search(
        r'<div\s+id=["\']Vacancies["\'][^>]*class=["\'][^"\']*job-vacancies[^"\']*["\'][^>]*>(.*?)<div\s+class=["\']faqs["\']',
        page,
        re.I | re.S,
    )
    if not vacancy_match:
        return

    # The page states Charlie's UK-wide hybrid policy and supplies substantial
    # careers context even though its cross-host detail pages are not admissible.
    context_match = re.search(
        r'<div[^>]+class=["\'][^"\']*centre-aligned-product-shot[^"\']*["\'][^>]*>(.*?)<div\s+id=["\']Vacancies["\']',
        page,
        re.I | re.S,
    )
    context = _text(context_match.group(1)) if context_match else ""

    seen = set()
    pattern = re.compile(
        r'<div[^>]+class=["\'][^"\']*accordion-button[^"\']*["\'][^>]*>\s*'
        r'.*?<h2[^>]+class=["\'][^"\']*faq-q[^"\']*["\'][^>]*>(.*?)</h2>\s*'
        r'.*?<a[^>]+href=["\'](https://jobs\.recruit\.charliehr\.com/charlie/[^"\']+/view-job)["\']',
        re.I | re.S,
    )

    for title_html, raw_url in pattern.findall(vacancy_match.group(1)):
        url = html.unescape(raw_url).strip()
        title = _text(title_html)
        if not title or url in seen:
            continue
        seen.add(url)

        description = context
        if not description:
            description = title

        slug_match = re.search(r"/charlie/([^/]+)/view-job", url)
        kwargs = {
            "url": url,
            "title": title,
            "company": "CharlieHR",
            "location": "UK",
            "description": description,
            "source": SITE,
            "apply_url": url,
            "workplace_type": "Hybrid",
        }
        if slug_match:
            kwargs["external_id"] = slug_match.group(1)

        yield kwargs
