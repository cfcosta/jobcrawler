import json
import re
import html
from urllib.parse import quote

BASE = "https://fa-extu-saasfaprod1.fa.ocs.oraclecloud.com"
SITE_NUMBER = "CX_1"
COMPANY = "Akamai"
# Candidate-facing vanity host (stored only, never fetched -> no EXTRA_HOSTS needed).
POSTING_HOST = "https://jobs.akamai.com"

_LIST_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_LIST_TAIL = ",sortBy=POSTING_DATES_DESC&expand=requisitionList.secondaryLocations,requisitionList.workLocation"
_PAGE_LIMIT = 200


def _listing_url(offset):
    return (
        BASE
        + _LIST_PATH
        + "?onlyData=true&finder=findReqs;siteNumber="
        + SITE_NUMBER
        + ",limit="
        + str(_PAGE_LIMIT)
        + ",offset="
        + str(offset)
        + _LIST_TAIL
    )


# The offset=0 page is the canonical listing endpoint; also used as the `source`.
SITE = _listing_url(0)
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"(?i)<\s*br\s*/?\s*>|</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _text(response):
    return response.text if hasattr(response, "text") else response


def _clean_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    lines = [_WS_RE.sub(" ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line) or None


def _first(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _locations(req, detail):
    seen = set()
    out = []

    def add(value):
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    add(_first(detail.get("PrimaryLocation"), req.get("PrimaryLocation")))

    for src in (req.get("secondaryLocations"), detail.get("secondaryLocations")):
        if isinstance(src, list):
            for loc in src:
                if isinstance(loc, dict):
                    add(
                        _first(
                            loc.get("Name"),
                            loc.get("LocationName"),
                            loc.get("location"),
                            loc.get("PrimaryLocation"),
                        )
                    )
                else:
                    add(str(loc))

    for src in (req.get("otherWorkLocations"), detail.get("otherWorkLocations")):
        if isinstance(src, list):
            for loc in src:
                if isinstance(loc, dict):
                    add(_first(loc.get("LocationName"), loc.get("Name")))
                else:
                    add(str(loc))

    return out


def _details_api(job_ids):
    # Oracle accepts a quoted comma-separated ById finder value.
    joined = ",".join(str(job_id) for job_id in job_ids)
    encoded_ids = quote(joined, safe=",")
    return (
        BASE
        + "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22"
        + encoded_ids
        + "%22,siteNumber="
        + SITE_NUMBER
    )


def _posting_url(job_id):
    encoded_id = quote(str(job_id), safe="")
    return POSTING_HOST + "/en/sites/" + SITE_NUMBER + "/job/" + encoded_id


def _fetch_details(ctx, ids):
    details = {}
    # Chunk so the URL and response stay bounded even as the board grows.
    for start in range(0, len(ids), 100):
        chunk = ids[start : start + 100]
        if not chunk:
            continue
        doc = json.loads(_text(ctx.fetch(_details_api(chunk))))
        for item in doc.get("items") or []:
            item_id = str(item.get("Id") or "").strip()
            if item_id:
                details[item_id] = item
    return details


def _fetch_requisitions(ctx):
    reqs = []
    offset = 0
    total = None
    while True:
        listing = json.loads(_text(ctx.fetch(_listing_url(offset))))
        item = (listing.get("items") or [{}])[0]
        if total is None:
            total = item.get("TotalJobsCount") or 0
        page = item.get("requisitionList") or []
        if not page:
            break
        reqs.extend(page)
        offset += _PAGE_LIMIT
        if offset >= total:
            break
    return reqs


def _build_job(req, job_id, detail):
    desc_parts = []
    for key in (
        "ExternalDescriptionStr",
        "ExternalQualificationsStr",
        "ExternalResponsibilitiesStr",
    ):
        cleaned = _clean_html(detail.get(key))
        if cleaned:
            desc_parts.append(cleaned)
    description = "\n\n".join(desc_parts)
    if not description:
        description = _clean_html(
            _first(detail.get("ShortDescriptionStr"), req.get("ShortDescriptionStr"))
        )
    if not description:
        return None

    location = _first(
        detail.get("PrimaryLocation"),
        req.get("PrimaryLocation"),
        detail.get("PrimaryLocationCountry"),
        req.get("PrimaryLocationCountry"),
    )
    title = _first(detail.get("Title"), req.get("Title"))
    if not location or not title:
        return None

    url = _posting_url(job_id)
    job = {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": job_id,
        "apply_url": url,
    }

    locs = _locations(req, detail)
    if locs:
        job["locations"] = locs

    posted_at = _first(detail.get("ExternalPostedStartDate"), req.get("PostedDate"))
    if posted_at:
        job["posted_at"] = posted_at

    employment_type = _first(
        detail.get("JobType"),
        req.get("JobType"),
        detail.get("WorkerType"),
        req.get("WorkerType"),
        detail.get("RequisitionType"),
    )
    if employment_type:
        job["employment_type"] = employment_type

    workplace_type = _first(
        detail.get("WorkplaceType"),
        req.get("WorkplaceType"),
        detail.get("WorkplaceTypeCode"),
        req.get("WorkplaceTypeCode"),
    )
    if workplace_type:
        job["workplace_type"] = workplace_type

    department = _first(
        detail.get("Department"),
        req.get("Department"),
        detail.get("JobFunction"),
        req.get("JobFunction"),
    )
    if department:
        job["department"] = department

    return job


def crawl(ctx):
    reqs = _fetch_requisitions(ctx)
    ids = [str(req.get("Id") or "").strip() for req in reqs if req.get("Id")]
    detail_by_id = _fetch_details(ctx, ids)

    for req in reqs:
        job_id = str(req.get("Id") or "").strip()
        if not job_id:
            continue
        job = _build_job(req, job_id, detail_by_id.get(job_id) or {})
        if job:
            yield job
