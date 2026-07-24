
import html
import re

from jobcrawler.runtime.contract import Job


SITE = "https://quickwit.io/jobs/distributed-software-engineer"
EXTRA_HOSTS = []
SITEMAP = "https://quickwit.io/sitemap.xml"


def clean_text(article):
    article = re.sub(
        r'<a\b[^>]*class=["\'][^"\']*\bhash-link\b[^"\']*["\'][^>]*>.*?</a>',
        "",
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )
    article = re.sub(
        r"<(?:script|style|svg)\b[^>]*>.*?</(?:script|style|svg)>",
        "",
        article,
        flags=re.IGNORECASE | re.DOTALL,
    )
    article = re.sub(r"<br\s*/?>", "\n", article, flags=re.IGNORECASE)
    article = re.sub(r"<li\b[^>]*>", "\n- ", article, flags=re.IGNORECASE)
    article = re.sub(
        r"</?(?:h[1-6]|p|ul|ol|section|div|article)\b[^>]*>",
        "\n",
        article,
        flags=re.IGNORECASE,
    )
    article = re.sub(r"<[^>]+>", "", article)
    article = html.unescape(article)

    lines = []
    for line in article.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    sitemap_response = ctx.fetch(SITEMAP)
    urls = re.findall(
        r"<loc>\s*(.*?)\s*</loc>",
        sitemap_response,
        flags=re.IGNORECASE | re.DOTALL,
    )

    seen = set()
    for raw_url in urls:
        url = html.unescape(raw_url.strip())
        if not re.fullmatch(r"https://quickwit\.io/jobs/[^/?#]+", url):
            continue
        if url in seen:
            continue
        seen.add(url)

        response = ctx.fetch(url)
        article_match = re.search(
            r"<article\b[^>]*>(.*?)</article>",
            response,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not article_match:
            continue

        article = article_match.group(1)
        title_match = re.search(
            r"<h1\b[^>]*>(.*?)</h1>",
            article,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not title_match:
            continue

        title = clean_text(title_match.group(1))
        description = clean_text(article)
        if not title or not description:
            continue

        kwargs = {
            "url": url,
            "title": title,
            "company": "Quickwit",
            "source": SITE,
            "location": "Europe",
            "description": description,
            "employment_type": "full-time",
            "workplace_type": "remote",
        }

        apply_match = re.search(
            r'href=["\'](mailto:[^"\']+)["\']',
            article,
            flags=re.IGNORECASE,
        )
        if apply_match:
            kwargs["apply_url"] = html.unescape(apply_match.group(1))

        salary_match = re.search(
            r"compensation range for the role is\s+80-110k€\s+\+\s+0\.25-1% of equity",
            description,
            flags=re.IGNORECASE,
        )
        if salary_match:
            kwargs["salary"] = "80-110k€ + 0.25-1% of equity"
            kwargs["salary_min"] = 80000
            kwargs["salary_max"] = 110000
            kwargs["salary_currency"] = "EUR"

        yield Job(**kwargs)
