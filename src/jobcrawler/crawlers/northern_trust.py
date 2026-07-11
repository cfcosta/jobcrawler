
import json
import re
from html import unescape

SITE = "https://ntrs.wd1.myworkdayjobs.com/wday/cxs/ntrs/northerntrust/jobs"
EXTRA_HOSTS = []

COMPANY = "Northern Trust"
CXS_BASE = "https://ntrs.wd1.myworkdayjobs.com/wday/cxs/ntrs/northerntrust"
PATHS = [('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/London-United-Kingdom/Accounting-Product-Lead_R156065-1', '/job/London-United-Kingdom/Accounting-Product-Lead_R156065-1'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Sr-Relationship-Manager--Insti_R151463', '/job/Chicago-IL/Sr-Relationship-Manager--Insti_R151463'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Tempe-AZ/Senior-Consultant--Non-Traditional-Third-Parties---Third-Party-Risk-Management_R157492', '/job/Tempe-AZ/Senior-Consultant--Non-Traditional-Third-Parties---Third-Party-Risk-Management_R157492'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Issue-Management-and-Risk-Event-Specialist--BU-Control_R154006', '/job/Chicago-IL/Issue-Management-and-Risk-Event-Specialist--BU-Control_R154006'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Principal--AI-Engineer_R157016', '/job/Chicago-IL/Principal--AI-Engineer_R157016'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Consultant--Treasury-COO---Strategy---Execution_R156168', '/job/Chicago-IL/Consultant--Treasury-COO---Strategy---Execution_R156168'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Sr-Accountant---Financial-Close-and-Consolidation_R157897', '/job/Chicago-IL/Sr-Accountant---Financial-Close-and-Consolidation_R157897'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Tempe-AZ/Procurement-and-Sustainability-Compliance-Consultant_R153007', '/job/Tempe-AZ/Procurement-and-Sustainability-Compliance-Consultant_R153007'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Tempe-AZ/Senior-Consultant--Concentration-Risk---Third-Party-Risk-Management_R152172', '/job/Tempe-AZ/Senior-Consultant--Concentration-Risk---Third-Party-Risk-Management_R152172'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Sr-Product-Manager---Enrollments---Entitlements_R157478', '/job/Chicago-IL/Sr-Product-Manager---Enrollments---Entitlements_R157478'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Limerick-Ireland/Senior-Analyst-Transfer-Agency_R156616', '/job/Limerick-Ireland/Senior-Analyst-Transfer-Agency_R156616'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/London-United-Kingdom/Global-KYC-Enhanced-Due-Diligence-Specialist_R157126', '/job/London-United-Kingdom/Global-KYC-Enhanced-Due-Diligence-Specialist_R157126'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Tempe-AZ/Principal--GRC-Cyber-Risk-Management_R154785-1', '/job/Tempe-AZ/Principal--GRC-Cyber-Risk-Management_R154785-1'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Luxembourg/Client-Servicing-Consultant_R142982', '/job/Luxembourg/Client-Servicing-Consultant_R142982'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Chicago-IL/Manager--Digital-Workforce---IAM-Product-Management--IAM-_R157945', '/job/Chicago-IL/Manager--Digital-Workforce---IAM-Product-Management--IAM-_R157945'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Pune-India/Infrastructure-Vulnerability-Management---Lead_R155142-2', '/job/Pune-India/Infrastructure-Vulnerability-Management---Lead_R155142-2'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Limerick-Ireland/Consultant--Private-Equity---Transfer-Agency_R152485', '/job/Limerick-Ireland/Consultant--Private-Equity---Transfer-Agency_R152485'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Limerick-Ireland/Manager---Private-Equity---Limerick-Hybrid_R153247', '/job/Limerick-Ireland/Manager---Private-Equity---Limerick-Hybrid_R153247'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Limerick-Ireland/Payments-Consultant_R156930', '/job/Limerick-Ireland/Payments-Consultant_R156930'), ('https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Limerick-Ireland/Accounting-Opportunities--Expression-of-Interest--Limerick---Hybrid_R158045-1', '/job/Limerick-Ireland/Accounting-Opportunities--Expression-of-Interest--Limerick---Hybrid_R158045-1')]


def _html_to_text(html):
    if not html:
        return None
    text = html
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|section|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _locations_from_info(info):
    values = []
    locs = info.get("jobRequisitionLocation")
    if isinstance(locs, list):
        for loc in locs:
            if isinstance(loc, dict) and loc.get("descriptor"):
                values.append(loc["descriptor"])
            elif isinstance(loc, str):
                values.append(loc)
    elif isinstance(locs, dict) and locs.get("descriptor"):
        values.append(locs["descriptor"])
    primary = info.get("location")
    if primary and primary not in values:
        values.insert(0, primary)
    return values


def crawl(ctx):
    for public_url, external_path in PATHS:
        detail = json.loads(ctx.fetch(CXS_BASE + external_path))
        info = detail.get("jobPostingInfo") or {}

        description = _html_to_text(info.get("jobDescription"))
        title = info.get("title")
        location = info.get("location")
        url = info.get("externalUrl") or public_url

        if not (title and location and description and url):
            continue

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = _locations_from_info(info)
        if len(locations) > 1:
            job["locations"] = locations

        if info.get("jobReqId"):
            job["external_id"] = info["jobReqId"]
        if info.get("externalUrl"):
            job["apply_url"] = info["externalUrl"]
        if info.get("startDate"):
            job["posted_at"] = info["startDate"]
        if info.get("timeType"):
            job["employment_type"] = info["timeType"]
        if info.get("remoteType"):
            job["workplace_type"] = info["remoteType"]

        yield job
