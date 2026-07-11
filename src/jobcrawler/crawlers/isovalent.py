
import json
import re
import html

SITE = "https://careers.cisco.com/widgets"
EXTRA_HOSTS = []

JOB_URLS = [
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2015298EXTERNALENGLOBAL/Customer-Reliability-Engineer",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2019603EXTERNALENGLOBAL/Customer-Reliability-Engineer-Hypershield-remote",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2004725EXTERNALENGLOBAL/Software-Engineering-Manager-Cilium-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012314EXTERNALENGLOBAL/Tetragon-Threat-Detection-Engineer-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012402EXTERNALENGLOBAL/Software-Engineering-Manager-Kubernetes-Load-Balancer-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2010115EXTERNALENGLOBAL/Full-Stack-Engineering-Manager-Ruby-on-Rails-Golang-Node-JS-React-Angular-Security-Networking-Domain-10-15-years",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2015865EXTERNALENGLOBAL/Sr-Customer-Success-Architect",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2015890EXTERNALENGLOBAL/Customer-Relibility-Engineer-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012410EXTERNALENGLOBAL/Principal-Engineer-Golang-Distributed-Systems-Isovalent-Secure-Workload-team",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012996EXTERNALENGLOBAL/Principal-Tetragon-Software-Engineer",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012400EXTERNALENGLOBAL/Tetragon-Engineering-Manager-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012512EXTERNALENGLOBAL/Senior-Software-Engineer-Tetragon",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2012994EXTERNALENGLOBAL/Tetragon-Software-Engineering-Technical-Leader",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2015895EXTERNALENGLOBAL/Sales-Leader-Isovalent",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2015900EXTERNALENGLOBAL/Account-Executive-Isovalent-Cloud-Native-Security",
    "https://careers.cisco.com/global/en/job/CISCISGLOBAL2011245EXTERNALENGLOBAL/Tetragon-Senior-Linux-Security-Engineer",
]


def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _json_ld_objects(page):
    pattern = re.compile(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.I | re.S,
    )
    for match in pattern.finditer(page or ""):
        raw = html.unescape(match.group(1).strip())
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                kind = item.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)


def _address_to_text(place):
    if not isinstance(place, dict):
        return None
    address = place.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        return ", ".join(str(p).strip() for p in parts if p)
    if isinstance(address, str):
        return address.strip() or None
    return None


def _employment_type(value):
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if v)
    return str(value).strip() if value else None


def _external_id(url, posting):
    ident = posting.get("identifier") if isinstance(posting, dict) else None
    if isinstance(ident, dict) and ident.get("value"):
        return str(ident.get("value"))
    match = re.search(r"CISCISGLOBAL(\d+)EXTERNALENGLOBAL", url)
    if match:
        return match.group(1)
    return None


def _job_from_page(ctx, url):
    page = ctx.fetch(url)
    posting = None
    for obj in _json_ld_objects(page):
        posting = obj
        break
    if not posting:
        return None

    locs = []
    raw_locs = posting.get("jobLocation")
    if isinstance(raw_locs, dict):
        raw_locs = [raw_locs]
    if isinstance(raw_locs, list):
        for loc in raw_locs:
            text = _address_to_text(loc)
            if text and text not in locs:
                locs.append(text)

    org = posting.get("hiringOrganization")
    company = "Cisco"
    if isinstance(org, dict) and org.get("name"):
        company = org.get("name")

    job = {
        "url": url,
        "title": posting.get("title"),
        "company": company,
        "location": locs[0] if locs else "Remote",
        "description": _strip_tags(posting.get("description")),
        "source": SITE,
    }

    if locs:
        job["locations"] = locs
    if posting.get("datePosted"):
        job["posted_at"] = posting.get("datePosted")
    if posting.get("validThrough"):
        job["expires_at"] = posting.get("validThrough")
    employment = _employment_type(posting.get("employmentType"))
    if employment:
        job["employment_type"] = employment
    if posting.get("occupationalCategory"):
        job["department"] = str(posting.get("occupationalCategory"))
    external_id = _external_id(url, posting)
    if external_id:
        job["external_id"] = external_id

    return {k: v for k, v in job.items() if v not in (None, "", [])}


def crawl(ctx):
    for url in JOB_URLS:
        job = _job_from_page(ctx, url)
        if job:
            yield job
