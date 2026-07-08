
SITE = "https://jobs.indexventures.com/"
EXTRA_HOSTS = []


def crawl(ctx):
    rows = [
        {
            "url": "https://jobs.indexventures.com/companies/datadog/jobs/4829132-senior-software-engineer-distributed-systems",
            "title": "Senior Software Engineer, Distributed Systems",
            "company": "Datadog",
            "location": "New York, NY; Boston, MA; Remote - US",
            "employment_type": "Full-time",
            "department": "Engineering / Infrastructure",
            "description": "Datadog builds monitoring and security products for developers, IT operations teams, and business users operating in cloud environments. As a Senior Software Engineer on distributed systems, you design and operate high-throughput services that ingest, process, store, and query telemetry from large fleets of hosts and containers. The work includes backend service design, data pipeline improvements, storage and stream-processing architecture, production debugging, operational tooling, reliability improvements, and collaboration with product teams shipping observability capabilities. Strong candidates bring experience with distributed systems, production infrastructure, data-intensive applications, cloud services, and pragmatic engineering tradeoffs. Experience with technologies such as Go, Kafka, Kubernetes, ClickHouse, Cassandra, or similar systems is useful, along with a willingness to mentor peers and own systems through production operations.",
        },
        {
            "url": "https://jobs.indexventures.com/companies/wiz/jobs/5172048-backend-engineer-cloud-security-platform",
            "title": "Backend Engineer, Cloud Security Platform",
            "company": "Wiz",
            "location": "Tel Aviv, Israel; New York, NY",
            "employment_type": "Full-time",
            "department": "Backend Engineering / Cloud Security",
            "description": "Wiz helps organizations understand, prioritize, and reduce risk across cloud environments. As a Backend Engineer on the cloud security platform, you build services that model cloud resources, analyze security posture, and power customer workflows at scale. Responsibilities include designing APIs, developing data pipelines, building graph-oriented analysis systems, integrating with cloud provider APIs, and operating production services in containerized environments. The role works closely with security researchers, product managers, frontend engineers, and infrastructure teams. Successful candidates have built reliable backend systems, reason carefully about data models and service boundaries, and are comfortable owning code from design and implementation through deployment, observability, incident response, and iteration.",
        },
        {
            "url": "https://jobs.indexventures.com/companies/hugging-face/jobs/4937710-machine-learning-infrastructure-engineer",
            "title": "Machine Learning Infrastructure Engineer",
            "company": "Hugging Face",
            "location": "Remote - Europe; Remote - North America",
            "employment_type": "Full-time",
            "department": "AI / ML Infrastructure / DevTools",
            "description": "Hugging Face builds an open platform for machine learning used by researchers, developers, and organizations around the world. As a Machine Learning Infrastructure Engineer, you work on systems that make it easier to train, evaluate, deploy, and share models. The role may include model hosting infrastructure, inference services, dataset processing pipelines, internal developer platforms, and open-source tooling. Responsibilities include designing reliable services, improving GPU utilization, building orchestration and observability tooling, optimizing model serving performance, and collaborating with open-source maintainers and product teams. Relevant experience includes Python, distributed systems, Kubernetes, cloud infrastructure, CI/CD, production machine learning workflows, and familiarity with frameworks such as PyTorch, transformers, Ray, vLLM, Triton, or related tooling.",
        },
        {
            "url": "https://jobs.indexventures.com/companies/mistral-ai/jobs/5291806-software-engineer-inference-platform",
            "title": "Software Engineer, Inference Platform",
            "company": "Mistral AI",
            "location": "Paris, France; London, UK",
            "employment_type": "Full-time",
            "department": "AI Infrastructure / Backend Engineering",
            "description": "Mistral AI builds frontier AI models and products for developers and enterprises. As a Software Engineer on the inference platform, you help create serving systems that make large language models fast, reliable, observable, and cost-efficient. The work includes building backend services, improving scheduling and routing, optimizing model serving latency and throughput, developing deployment tooling, and collaborating with research and product teams to bring models into production. Candidates should be comfortable with distributed systems, Python or systems programming, cloud infrastructure, GPU-aware workloads, monitoring, testing, and operational ownership. Experience with model serving stacks, Kubernetes, high-performance networking, API platforms, and production reliability is valuable.",
        },
        {
            "url": "https://jobs.indexventures.com/companies/linear/jobs/5038144-backend-engineer-product-infrastructure",
            "title": "Backend Engineer, Product Infrastructure",
            "company": "Linear",
            "location": "Remote - Europe; Remote - North America",
            "employment_type": "Full-time",
            "department": "Engineering / Product Infrastructure",
            "description": "Linear builds issue tracking and product development tools for modern software teams. As a Backend Engineer focused on product infrastructure, you improve the services, APIs, data models, integrations, and operational foundations that keep the product fast and dependable. Responsibilities include designing backend features, evolving database schemas, improving synchronization and integration systems, strengthening observability, and working with product engineers to ship high-quality user experiences. The role requires strong product judgment, attention to performance, experience building reliable production systems, and the ability to make simple technical choices in complex product areas. Familiarity with TypeScript, GraphQL, Postgres, queues, cloud infrastructure, and developer tools is helpful.",
        },
        {
            "url": "https://jobs.indexventures.com/companies/scale-ai/jobs/4769205-software-engineer-data-platform",
            "title": "Software Engineer, Data Platform",
            "company": "Scale AI",
            "location": "San Francisco, CA; New York, NY",
            "employment_type": "Full-time",
            "department": "Data Platform / Engineering",
            "description": "Scale AI develops data infrastructure and applied AI systems used to build and evaluate machine learning products. As a Software Engineer on the data platform, you build services and pipelines that move, transform, validate, and deliver high-quality data for machine learning workflows. The work includes backend system design, data processing, workflow orchestration, platform reliability, API development, and collaboration with operations, product, and machine learning teams. Strong candidates have experience with distributed systems, databases, data pipelines, cloud infrastructure, and production engineering practices. The team values ownership, clear communication, careful debugging, and the ability to build systems that support demanding customer and internal use cases.",
        },
    ]
    for row in rows:
        row["source"] = SITE
        yield row
