import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

SITE = "https://jobs.gem.com/api/public/graphql"
EXTRA_HOSTS = []

BOARD_ID = "rootly"
COMPANY = "Rootly"

LIST_QUERY = "query JobBoardList($boardId:String!){oatsExternalJobPostings(boardId:$boardId){jobPostings{id extId title locations{id name city isoCountry isRemote} job{department{name} locationType employmentType}}}}"
DETAIL_FIELDS = "extId title descriptionHtml firstPublishedTsSec locations{id name city isoCountry isRemote} job{department{name} locationType employmentType}"


def _graphql_url(operation_name, variables, query):
    params = {
        "operationName": operation_name,
        "variables": json.dumps(variables, separators=(",", ":")),
        "query": query,
    }
    return SITE + "?" + urlencode(params)


def _fetch_graphql(ctx, operation_name, variables, query):
    return json.loads(ctx.fetch(_graphql_url(operation_name, variables, query)))


def _clean_text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _iso_from_epoch(seconds):
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _norm_workplace(value):
    if not value:
        return None
    mapping = {"REMOTE": "remote", "HYBRID": "hybrid", "ONSITE": "onsite"}
    return mapping.get(value, value)


def _norm_employment(value):
    if not value:
        return None
    return value.replace("_", " ").title()


def _loc_display(loc):
    name = (loc.get("name") or "").strip()
    city = (loc.get("city") or "").strip()
    if name and name.lower() != "office":
        return name
    if city:
        return city
    return name or None


def _location_names(posting):
    names = []
    for loc in posting.get("locations") or []:
        name = _loc_display(loc)
        if name and name not in names:
            names.append(name)
    return names


def _batch_detail_query(postings):
    parts = []
    for index, posting in enumerate(postings):
        ext_id = posting.get("extId")
        if not ext_id:
            continue
        parts.append(
            "j%s:oatsExternalJobPosting(boardId:%s,extId:%s){%s}"
            % (index, json.dumps(BOARD_ID), json.dumps(ext_id), DETAIL_FIELDS)
        )
    if not parts:
        return None
    return "query BatchedDetails{" + "".join(parts) + "}"


def crawl(ctx):
    listing = _fetch_graphql(
        ctx,
        "JobBoardList",
        {"boardId": BOARD_ID},
        LIST_QUERY,
    )
    postings = (
        listing.get("data", {})
        .get("oatsExternalJobPostings", {})
        .get("jobPostings", [])
    )

    detail_query = _batch_detail_query(postings)
    detail_map = {}
    if detail_query:
        detail_map = _fetch_graphql(ctx, "BatchedDetails", {}, detail_query).get(
            "data", {}
        )

    for index, summary in enumerate(postings):
        ext_id = summary.get("extId")
        if not ext_id:
            continue

        detail = detail_map.get("j%s" % index) or summary
        title = (detail.get("title") or summary.get("title") or "").strip()
        description = _clean_text(detail.get("descriptionHtml"))
        if not title or not description:
            continue

        locations = _location_names(detail) or _location_names(summary)
        location = locations[0] if locations else None

        job = detail.get("job") or summary.get("job") or {}
        department = ((job.get("department") or {}).get("name") or "").strip() or None
        workplace_type = _norm_workplace(job.get("locationType"))
        employment_type = _norm_employment(job.get("employmentType"))
        posted_at = _iso_from_epoch(detail.get("firstPublishedTsSec"))

        item = {
            "url": "https://jobs.gem.com/rootly/" + ext_id,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": ext_id,
        }
        if locations:
            item["locations"] = locations
        if department:
            item["department"] = department
        if workplace_type:
            item["workplace_type"] = workplace_type
        if employment_type:
            item["employment_type"] = employment_type
        if posted_at:
            item["posted_at"] = posted_at

        yield item
