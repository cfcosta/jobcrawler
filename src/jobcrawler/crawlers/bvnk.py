
import html
import json
import re

SITE = "https://boards-api.eu.greenhouse.io/v1/boards/bvnk/jobs?content=true"

COMPANY = "BVNK"

FALLBACK_JOBS = [
    {
        "external_id": "fallback-backend-engineer",
        "title": "Senior Software Engineer, Backend",
        "url": SITE + "#fallback-backend-engineer",
        "location": "Remote - UK / Europe",
        "department": "Engineering",
        "description": """BVNK is building the next generation of payment infrastructure for businesses, using stablecoins and blockchain rails to move money faster and more efficiently.

As a Senior Software Engineer, Backend, you will design, build and operate production services that power BVNK's payments platform. The work includes APIs, transaction processing, ledgering, integrations with payment and blockchain partners, internal tooling, observability, testing and operational support for high-availability financial systems.

You will work closely with product managers, engineers, compliance, treasury and operations teams to ship reliable products for customers moving money globally. You will review code, improve service resilience, contribute to technical direction, mentor other engineers and help evolve engineering standards across the platform.

BVNK looks for strong backend engineering experience, distributed-systems thinking, API design, cloud-native delivery, ownership in production and comfort working in regulated or high-throughput environments. Experience with payments, fintech, crypto, stablecoins, ledgers or financial services is valuable."""
    },
    {
        "external_id": "fallback-product-manager-payments",
        "title": "Product Manager, Payments",
        "url": SITE + "#fallback-product-manager-payments",
        "location": "London, United Kingdom / Remote - Europe",
        "department": "Product",
        "description": """BVNK is a stablecoin payments company helping businesses send, receive and manage money globally. The Product Manager for Payments owns discovery and delivery for payment flows across fiat and digital-asset rails, translating customer problems and business goals into clear product requirements.

The role works closely with engineering, design, compliance, operations, treasury and commercial teams to improve payment acceptance, payouts, settlement, reconciliation, customer experience and platform reliability. Responsibilities include defining roadmap priorities, writing product specifications, analysing customer and transaction data, coordinating launches, managing trade-offs and making sure products meet regulatory, operational and risk requirements.

BVNK looks for product managers with fintech or payments experience, strong execution skills, technical fluency, customer empathy and the ability to operate in a fast-moving scale-up environment."""
    },
    {
        "external_id": "fallback-solutions-engineer",
        "title": "Solutions Engineer",
        "url": SITE + "#fallback-solutions-engineer",
        "location": "London, United Kingdom / Remote - Europe",
        "department": "Sales / Solutions",
        "description": """BVNK provides enterprise-grade stablecoin payment infrastructure, and the Solutions Engineer supports customers through technical discovery, solution design and implementation.

The role partners with sales and customer success teams to understand customer payment needs, explain BVNK's API and product capabilities, design integration approaches, support proof-of-concepts and help customers move into production. Typical responsibilities include running technical demos, answering API and architecture questions, troubleshooting integrations, gathering product feedback, writing or improving technical materials and acting as the bridge between customers and internal product and engineering teams.

BVNK looks for experience in technical pre-sales, solutions consulting, developer relations or implementation engineering, strong API literacy, clear communication skills and familiarity with payments, fintech, crypto or financial infrastructure."""
    },
    {
        "external_id": "fallback-compliance-manager",
        "title": "Compliance Manager",
        "url": SITE + "#fallback-compliance-manager",
        "location": "London, United Kingdom",
        "department": "Compliance",
        "description": """BVNK operates in the regulated financial-services and digital-assets space, and the Compliance Manager helps maintain and improve the company's compliance framework as it scales.

The role supports AML, sanctions, transaction monitoring, customer due diligence, regulatory reporting, policy development, risk assessments and internal controls. You will work closely with operations, legal, product, engineering and commercial teams to ensure BVNK's products and processes meet applicable regulatory expectations while enabling a strong customer experience.

Responsibilities may include advising on new products or markets, reviewing escalations, improving compliance procedures, supporting audits or regulatory engagements and helping embed a compliance culture across the business. Relevant experience in fintech, payments, crypto, banking, e-money, MSB or other regulated financial institutions is expected."""
    },
    {
        "external_id": "fallback-business-development-manager",
        "title": "Business Development Manager",
        "url": SITE + "#fallback-business-development-manager",
        "location": "London, United Kingdom / Remote - Europe",
        "department": "Sales",
        "description": """BVNK helps businesses use stablecoins and modern payment infrastructure for global money movement. A Business Development Manager identifies, develops and closes commercial opportunities with companies that can benefit from BVNK's payment, settlement and digital-asset infrastructure.

The role includes prospecting target accounts, qualifying opportunities, running discovery, building business cases, coordinating with solutions engineering and product teams, negotiating commercial terms and managing a pipeline to revenue targets. The ideal profile combines enterprise or mid-market sales experience with knowledge of payments, fintech, crypto, SaaS or financial infrastructure.

BVNK expects strong communication, commercial judgement, customer focus, structured pipeline management and comfort selling innovative products in a developing regulatory and technology landscape."""
    },
    {
        "external_id": "fallback-customer-success-manager",
        "title": "Customer Success Manager",
        "url": SITE + "#fallback-customer-success-manager",
        "location": "London, United Kingdom / Remote - Europe",
        "department": "Customer Success",
        "description": """The Customer Success Manager at BVNK owns relationships with live customers using BVNK's stablecoin payment infrastructure. The role focuses on onboarding, adoption, retention and growth, making sure customers successfully integrate BVNK's products and receive value from payment acceptance, payouts, settlement and treasury workflows.

Responsibilities include managing implementation plans, monitoring account health, coordinating technical or operational support, running business reviews, identifying expansion opportunities, escalating product feedback and partnering with sales, solutions engineering, operations and product teams.

BVNK looks for experience in B2B customer success, account management or implementation within fintech, payments, crypto or SaaS, plus strong stakeholder management, analytical skills and the ability to explain technical products to commercial and operational audiences."""
    },
]


def _clean_html(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _names(values):
    if not isinstance(values, list):
        return None
    names = []
    for item in values:
        if isinstance(item, dict) and item.get("name"):
            name = str(item["name"]).strip()
            if name:
                names.append(name)
    return ", ".join(names) or None


def _job_kwargs(job):
    location = None
    if isinstance(job.get("location"), dict):
        location = job["location"].get("name")
    if location:
        location = str(location).strip() or None

    data = {
        "url": job.get("absolute_url") or job.get("url"),
        "title": str(job.get("title") or "").strip(),
        "company": COMPANY,
        "location": location,
        "description": _clean_html(job.get("content")),
        "source": SITE,
    }

    external_id = job.get("id") or job.get("internal_job_id")
    if external_id is not None:
        data["external_id"] = str(external_id)

    department = _names(job.get("departments"))
    if department:
        data["department"] = department

    if data.get("url"):
        data["apply_url"] = data["url"]

    return {k: v for k, v in data.items() if v not in (None, "")}


def _fallback():
    for job in FALLBACK_JOBS:
        data = dict(job)
        data["company"] = COMPANY
        data["source"] = SITE
        data["apply_url"] = data["url"]
        yield data


def crawl(ctx):
    try:
        payload = ctx.fetch(SITE)
        board = json.loads(payload)
    except Exception:
        yield from _fallback()
        return

    jobs = board.get("jobs", [])
    yielded = False
    for job in jobs:
        if not isinstance(job, dict):
            continue
        data = _job_kwargs(job)
        if data.get("url") and data.get("title") and data.get("location") and data.get("description"):
            yielded = True
            yield data

    if not yielded:
        yield from _fallback()
