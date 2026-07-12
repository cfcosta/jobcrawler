
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/riskified/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(response)

    for posting in data.get("jobs", []):
        kwargs = {
            "url": posting["absolute_url"],
            "title": posting["title"].strip(),
            "company": posting.get("company_name") or "Riskified",
            "location": posting["location"]["name"],
            "description": _plain_text(posting["content"]),
            "source": SITE,
            "external_id": str(posting["id"]),
            "apply_url": posting["absolute_url"],
        }

        if posting.get("first_published"):
            kwargs["posted_at"] = posting["first_published"]

        departments = [
            item["name"]
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        if departments:
            kwargs["department"] = ", ".join(departments)

        offices = [
            item["name"]
            for item in posting.get("offices", [])
            if item.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                item["min_cents"] / 100
                for item in ranges
                if item.get("min_cents") is not None
            ]
            maximums = [
                item["max_cents"] / 100
                for item in ranges
                if item.get("max_cents") is not None
            ]
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        yield kwargs
