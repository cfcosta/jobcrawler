import html
import json
import re
from datetime import datetime, timezone

SITE = "https://jobs.gem.com/api/public/graphql"
EXTRA_HOSTS = []

BOARD_ID = "ocient-inc-"
COMPANY = "Ocient"
LIST_QUERY = """query JobBoardList($boardId:String!){oatsExternalJobPostings(boardId:$boardId){jobPostings{id extId title locations{name city isoCountry isRemote} job{department{name} locationType employmentType}}}}"""
DETAIL_QUERY = """query ExternalJobPostingQuery($boardId:String!,$extId:String!){oatsExternalJobPosting(boardId:$boardId,extId:$extId){descriptionHtml compensationHtml firstPublishedTsSec companyUrl}}"""


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|div|li|p|tr|h[1-6])\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(?:div|li|p|tr|h[1-6])\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _json_response(response):
    if isinstance(response, (dict, list)):
        return response
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        response = response.text
    return json.loads(response)


def _post(ctx, operation_name, variables, query):
    payload = {
        "operationName": operation_name,
        "variables": variables,
        "query": query,
    }
    return _json_response(ctx.fetch(SITE, method="POST", json=payload))


def _published_at(seconds):
    if seconds is None:
        return None
    return datetime.fromtimestamp(
        seconds, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def crawl(ctx):
    listing = _post(ctx, "JobBoardList", {"boardId": BOARD_ID}, LIST_QUERY)
    postings = listing["data"]["oatsExternalJobPostings"]["jobPostings"]

    for posting in postings:
        ext_id = posting["extId"]
        detail_payload = _post(
            ctx,
            "ExternalJobPostingQuery",
            {"boardId": BOARD_ID, "extId": ext_id},
            DETAIL_QUERY,
        )
        detail = detail_payload["data"]["oatsExternalJobPosting"]
        location_records = posting.get("locations") or []
        location_names = [
            item["name"] for item in location_records if item.get("name")
        ]
        primary_location = location_names[0] if location_names else "Remote"
        job_data = posting.get("job") or {}
        department = (job_data.get("department") or {}).get("name")
        url = f"https://jobs.gem.com/{BOARD_ID}/{ext_id}"

        yield dict(
            url=url,
            title=posting["title"].strip(),
            company=COMPANY,
            location=primary_location,
            locations=location_names or None,
            description=_text(detail.get("descriptionHtml")),
            source="Gem",
            external_id=ext_id,
            apply_url=url,
            department=department,
            employment_type=job_data.get("employmentType"),
            workplace_type=job_data.get("locationType"),
            posted_at=_published_at(detail.get("firstPublishedTsSec")),
        )
