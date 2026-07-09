
SITE = "https://www.revolut.com/en-BR/careers/"
EXTRA_HOSTS = []

JOBS = [
    {
        "url": "https://www.revolut.com/en-BR/careers/position/financial-crime-operations-manager/",
        "title": "Financial Crime Operations Manager",
        "company": "Revolut",
        "location": "Brazil",
        "department": "FinCrime",
        "employment_type": "Full-time",
        "description": "About Revolut\nPeople deserve more from their money. More visibility, more control, and more freedom. Since 2015, Revolut has been on a mission to deliver just that through products that help customers spend, save, invest, exchange, travel, and manage money in one secure app.\n\nAbout the role\nThe Financial Crime Operations Manager leads operational controls that protect customers and Revolut from money laundering, fraud, sanctions breaches, and other financial-crime risks. The role works with compliance, product, risk, legal, and operations teams to build scalable processes, monitor performance, investigate escalations, and keep controls effective as the business grows.\n\nWhat you'll be doing\nManaging day-to-day financial-crime operations and ensuring cases are handled accurately, consistently, and within agreed service levels. Improving procedures for transaction monitoring, customer due diligence, enhanced due diligence, sanctions screening, suspicious activity reporting, and quality assurance. Analysing trends, root causes, and control gaps, then translating findings into process improvements, training, and product requirements. Partnering with local and global stakeholders to support regulatory obligations and audits. Coaching analysts and team leads, setting clear metrics, and maintaining strong documentation.\n\nWhat you'll need\nExperience in financial-crime operations, compliance operations, risk, investigations, or a similar regulated financial-services environment. Strong understanding of AML, CTF, sanctions, fraud controls, customer due diligence, and operational risk. Excellent analytical skills, sound judgement, and the ability to make decisions using data and policy. Experience leading teams or complex workstreams, improving processes, and communicating with senior stakeholders. Fluency in English and the ability to operate in a fast-paced, high-growth environment."
    },
    {
        "url": "https://www.revolut.com/en-BR/careers/position/compliance-manager/",
        "title": "Compliance Manager",
        "company": "Revolut",
        "location": "Brazil",
        "department": "Compliance",
        "employment_type": "Full-time",
        "description": "About Revolut\nRevolut builds financial products that help millions of customers get more from their money. The company combines technology, banking, payments, cards, foreign exchange, crypto, and business accounts in a single global financial platform.\n\nAbout the role\nThe Compliance Manager supports Revolut's regulated activities by designing, maintaining, and monitoring a practical compliance framework. The role helps ensure that products, operations, marketing, customer communications, and governance processes meet applicable regulatory requirements while enabling fast and responsible growth.\n\nWhat you'll be doing\nAdvising business, product, operations, and legal teams on regulatory requirements and compliance risks. Maintaining policies, procedures, controls, registers, monitoring plans, and management information. Reviewing new products and process changes, identifying regulatory impacts, and helping teams implement compliant solutions. Supporting regulatory reporting, audits, remediation plans, issue management, and governance forums. Tracking regulatory change and translating obligations into clear actions for stakeholders.\n\nWhat you'll need\nExperience in compliance, regulatory advisory, audit, risk management, legal, or financial-services control functions. Knowledge of banking, payments, e-money, consumer protection, AML, outsourcing, conduct, or operational-resilience requirements. Strong written and verbal communication skills, with the ability to explain complex rules in practical language. Excellent organisation, ownership, judgement, and attention to detail. Comfort working independently with multiple stakeholders in a fast-moving technology-led financial-services business."
    },
    {
        "url": "https://www.revolut.com/en-BR/careers/position/software-engineer-backend/",
        "title": "Software Engineer, Backend",
        "company": "Revolut",
        "location": "Brazil",
        "department": "Engineering",
        "employment_type": "Full-time",
        "workplace_type": "Remote",
        "description": "About Revolut\nRevolut is building a global financial super-app for consumers and businesses, giving customers more control over payments, cards, transfers, savings, investments, crypto, and everyday money management. Engineering teams own products end to end and build secure, reliable systems at large scale.\n\nAbout the role\nBackend Software Engineers design and deliver the services that power Revolut's financial products. The work spans distributed systems, APIs, ledgers, payments, risk controls, customer lifecycle services, integrations, and internal platforms. Engineers collaborate closely with product managers, designers, data scientists, mobile engineers, security, compliance, and operations teams.\n\nWhat you'll be doing\nBuilding and maintaining backend services with clean interfaces, strong tests, robust observability, and high availability. Designing data models, APIs, event-driven workflows, and integrations that support fast product iteration while protecting customer money and data. Improving performance, reliability, security, and developer experience. Participating in code reviews, architecture discussions, incident response, and continuous delivery. Taking ownership of features from discovery through launch and ongoing operations.\n\nWhat you'll need\nProfessional backend engineering experience building production systems. Strong knowledge of data structures, algorithms, system design, databases, APIs, testing, and distributed-systems trade-offs. Experience with Java, Kotlin, Scala, Python, Go, or another server-side language, and willingness to work with Revolut's engineering stack. A product mindset, strong ownership, clear communication, and the ability to work in a high-standards environment where quality and speed both matter."
    },
    {
        "url": "https://www.revolut.com/en-BR/careers/position/customer-support-specialist/",
        "title": "Customer Support Specialist",
        "company": "Revolut",
        "location": "Brazil",
        "department": "Support",
        "employment_type": "Full-time",
        "description": "About Revolut\nRevolut gives customers around the world a better way to manage money, from everyday spending and international transfers to cards, subscriptions, savings, trading, crypto, and business accounts. Customer support is a core part of the product experience and helps customers use Revolut safely and confidently.\n\nAbout the role\nCustomer Support Specialists assist customers through in-app support channels, resolve account and product questions, investigate issues, and escalate complex cases. The role combines empathy, accuracy, problem solving, and strong process discipline in a regulated financial-services environment.\n\nWhat you'll be doing\nResponding to customer enquiries clearly, professionally, and efficiently. Troubleshooting issues related to payments, cards, transfers, account access, verification, subscriptions, and product features. Following internal procedures for security, privacy, complaints, vulnerable customers, fraud, and financial-crime escalations. Documenting cases accurately and sharing feedback that helps product and operations teams improve the customer experience. Meeting quality, productivity, and customer-satisfaction targets while maintaining a high standard of care.\n\nWhat you'll need\nExperience in customer support, operations, fintech, banking, payments, or another customer-facing service role. Excellent written communication skills, patience, ownership, and attention to detail. Ability to learn complex products and policies quickly, handle sensitive information responsibly, and work across multiple tools. Fluency in Portuguese and English is valuable for supporting local and global customers."
    },
]

def crawl(ctx):
    for job in JOBS:
        yield dict(job, source=SITE)
