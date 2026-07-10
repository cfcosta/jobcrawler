
import json
import re
import html
from urllib.parse import quote

SITE = "https://epdm.fa.la1.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber=CX_1,limit=200,sortBy=POSTING_DATES_DESC&expand=requisitionList.secondaryLocations,requisitionList.workLocation"
EXTRA_HOSTS = []

COMPANY = "PicPay"
SITE_NUMBER = "CX_1"
BASE = "https://epdm.fa.la1.oraclecloud.com"

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
                    add(_first(loc.get("Name"), loc.get("LocationName"), loc.get("location"), loc.get("PrimaryLocation")))
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
    # Oracle accepts all IDs in one quoted comma-separated ById finder value.
    joined = ",".join(str(job_id) for job_id in job_ids)
    encoded_ids = quote(joined, safe=",")
    return BASE + "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22" + encoded_ids + "%22,siteNumber=" + SITE_NUMBER

def _posting_url(job_id):
    encoded_id = quote(str(job_id), safe="")
    return BASE + "/hcmUI/CandidateExperience/pt-BR/sites/PicPay/job/" + encoded_id

def _fetch_details(ctx, ids):
    details = {}
    if not ids:
        return details
    doc = json.loads(_text(ctx.fetch(_details_api(ids))))
    for item in doc.get("items") or []:
        item_id = str(item.get("Id") or "").strip()
        if item_id:
            details[item_id] = item
    return details

def _build_job(req, job_id, detail):
    desc_parts = []
    for key in ("ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr"):
        cleaned = _clean_html(detail.get(key))
        if cleaned:
            desc_parts.append(cleaned)
    description = "\n\n".join(desc_parts)
    if not description:
        description = _clean_html(_first(detail.get("ShortDescriptionStr"), req.get("ShortDescriptionStr")))
    if not description:
        return None

    location = _first(detail.get("PrimaryLocation"), req.get("PrimaryLocation"), detail.get("PrimaryLocationCountry"), req.get("PrimaryLocationCountry"))
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

    expires_at = _first(detail.get("ExternalPostedEndDate"), req.get("PostingEndDate"))
    if expires_at:
        job["expires_at"] = expires_at

    employment_type = _first(detail.get("JobType"), req.get("JobType"), detail.get("WorkerType"), req.get("WorkerType"), detail.get("RequisitionType"))
    if employment_type:
        job["employment_type"] = employment_type

    workplace_type = _first(detail.get("WorkplaceType"), req.get("WorkplaceType"), detail.get("WorkplaceTypeCode"), req.get("WorkplaceTypeCode"))
    if workplace_type:
        job["workplace_type"] = workplace_type

    department = _first(detail.get("Department"), req.get("Department"), detail.get("Category"), req.get("JobFunction"))
    if department:
        job["department"] = department

    return job

def crawl(ctx):
    listing = json.loads(_text(ctx.fetch(SITE)))
    reqs = listing.get("items", [{}])[0].get("requisitionList", [])
    ids = [str(req.get("Id") or "").strip() for req in reqs if req.get("Id")]
    detail_by_id = _fetch_details(ctx, ids)

    for req in reqs:
        job_id = str(req.get("Id") or "").strip()
        if not job_id:
            continue
        job = _build_job(req, job_id, detail_by_id.get(job_id) or {})
        if job:
            yield job
