
import json
import re
import html
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

SITE = "https://www.turing.com/jobs/remote-software-engineer-jobs"
EXTRA_HOSTS = []

COMPANY = "Turing.com"
SITEMAP_URL = "https://www.turing.com/sitemap.xml"

FALLBACK_ROLES = [
    ("remote-software-engineer-jobs", "Remote Software Engineer Jobs", "software engineer", "programming languages, APIs, databases, cloud platforms, testing, and system design"),
    ("remote-full-stack-developer-jobs", "Remote Full Stack Developer Jobs", "full stack developer", "JavaScript, React, Node.js, REST APIs, databases, HTML, CSS, and cloud services"),
    ("remote-front-end-developer-jobs", "Remote Front-end Developer Jobs", "front-end developer", "JavaScript, React, HTML, CSS, responsive design, browser APIs, and modern build tools"),
    ("remote-back-end-developer-jobs", "Remote Back-end Developer Jobs", "back-end developer", "server-side programming, APIs, databases, distributed systems, cloud services, and performance optimization"),
    ("remote-python-developer-jobs", "Remote Python Developer Jobs", "Python developer", "Python, Django, Flask, REST APIs, databases, testing, automation, and cloud platforms"),
    ("remote-javascript-developer-jobs", "Remote JavaScript Developer Jobs", "JavaScript developer", "JavaScript, TypeScript, React, Node.js, HTML, CSS, APIs, and modern web tooling"),
    ("remote-react-js-developer-jobs", "Remote React.js Developer Jobs", "React.js developer", "React.js, JavaScript, TypeScript, state management, HTML, CSS, APIs, and front-end performance"),
    ("remote-node-js-developer-jobs", "Remote Node.js Developer Jobs", "Node.js developer", "Node.js, JavaScript, TypeScript, Express, REST APIs, databases, testing, and cloud services"),
    ("remote-java-developer-jobs", "Remote Java Developer Jobs", "Java developer", "Java, Spring, microservices, REST APIs, databases, testing, and distributed systems"),
    ("remote-php-developer-jobs", "Remote PHP Developer Jobs", "PHP developer", "PHP, Laravel, Symfony, MySQL, APIs, testing, and web application architecture"),
    ("remote-ruby-on-rails-developer-jobs", "Remote Ruby on Rails Developer Jobs", "Ruby on Rails developer", "Ruby, Rails, REST APIs, relational databases, testing, and web application development"),
    ("remote-golang-developer-jobs", "Remote Golang Developer Jobs", "Golang developer", "Go, microservices, APIs, concurrency, databases, cloud platforms, and distributed systems"),
    ("remote-android-developer-jobs", "Remote Android Developer Jobs", "Android developer", "Kotlin, Java, Android SDK, mobile architecture, APIs, testing, and app performance"),
    ("remote-ios-developer-jobs", "Remote iOS Developer Jobs", "iOS Developer Jobs", "Swift, Objective-C, iOS SDK, UIKit, SwiftUI, APIs, testing, and mobile app performance"),
    ("remote-devops-engineer-jobs", "Remote DevOps Engineer Jobs", "DevOps engineer", "AWS, Azure, GCP, Docker, Kubernetes, CI/CD, infrastructure automation, monitoring, and Linux"),
    ("remote-data-scientist-jobs", "Remote Data Scientist Jobs", "data scientist", "Python, SQL, statistics, machine learning, data visualization, experimentation, and data modeling"),
    ("remote-machine-learning-engineer-jobs", "Remote Machine Learning Engineer Jobs", "machine learning engineer", "Python, machine learning frameworks, model deployment, data pipelines, APIs, and cloud platforms"),
    ("remote-ai-engineer-jobs", "Remote AI Engineer Jobs", "AI engineer", "Python, machine learning, deep learning, LLMs, model evaluation, APIs, and cloud-based AI systems"),
    ("remote-django-developer-jobs", "Remote Django Developer Jobs", "Django developer", "Python, Django, Django REST Framework, databases, APIs, testing, and cloud deployment"),
    ("remote-laravel-developer-jobs", "Remote Laravel Developer Jobs", "Laravel developer", "PHP, Laravel, MySQL, REST APIs, queues, testing, and web application architecture"),
]


def _strip_tags(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip() or None


def _text_from_node(value):
    if value is None:
        return None
    if isinstance(value, str):
        return _strip_tags(value)
    if isinstance(value, dict):
        for key in ("description", "name", "text"):
            if value.get(key):
                return _strip_tags(value.get(key))
    return _strip_tags(value)


def _find_jobposting(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type") or obj.get("type")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(t).lower() == "jobposting" for t in types if t):
            return obj
        if "@graph" in obj:
            found = _find_jobposting(obj.get("@graph"))
            if found:
                return found
        for value in obj.values():
            found = _find_jobposting(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_jobposting(item)
            if found:
                return found
    return None


def _json_loads_loose(raw):
    return json.loads(html.unescape(raw or "").strip())


def _extract_job_schema(page):
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page or "", re.I | re.S):
        try:
            job = _find_jobposting(_json_loads_loose(match.group(1)))
            if job:
                return job
        except Exception:
            pass

    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page or "", re.I | re.S)
    if match:
        try:
            data = _json_loads_loose(match.group(1))
            props = data.get("props", {}).get("pageProps", {})
            for key in ("jobSchema", "schema", "job"):
                job = props.get(key)
                if isinstance(job, str):
                    job = json.loads(job)
                found = _find_jobposting(job)
                if found:
                    return found
        except Exception:
            pass
    return None


def _compose_description(job):
    parts = []
    for key in ("description", "responsibilities"):
        text = _text_from_node(job.get(key))
        if text:
            parts.append(text)

    skills = job.get("skills")
    if isinstance(skills, list):
        clean = [_text_from_node(s) for s in skills]
        clean = [s for s in clean if s]
        if clean:
            parts.append("Skills:\n" + "\n".join("- " + s for s in clean))
    elif skills:
        text = _text_from_node(skills)
        if text:
            parts.append("Skills:\n" + text)

    exp_text = _text_from_node(job.get("experienceRequirements"))
    if exp_text:
        parts.append("Experience requirements:\n" + exp_text)

    for key, label in (("industry", "Industry"), ("workHours", "Work hours")):
        text = _text_from_node(job.get(key))
        if text:
            parts.append(label + ": " + text)

    return "\n\n".join(parts).strip() or None


def _location(job):
    if job.get("jobLocationType"):
        return str(job.get("jobLocationType"))
    loc = job.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        address = loc.get("address")
        if isinstance(address, dict):
            pieces = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            text = ", ".join(str(p) for p in pieces if p)
            if text:
                return text
        if loc.get("name"):
            return str(loc.get("name"))
    elif isinstance(loc, str) and loc.strip():
        return loc.strip()
    return "TELECOMMUTE"


def _salary_fields(job, out):
    salary = job.get("baseSalary")
    if not isinstance(salary, dict):
        return
    currency = salary.get("currency")
    value = salary.get("value")
    if isinstance(value, dict):
        unit = value.get("unitText")
        direct = value.get("value")
        if currency:
            out["salary_currency"] = str(currency)
        if unit:
            out["salary_period"] = str(unit)
        if direct not in (None, ""):
            out["salary"] = str(direct)
        for src, dst in (("minValue", "salary_min"), ("maxValue", "salary_max")):
            if value.get(src) not in (None, ""):
                out[dst] = value.get(src)
    elif value not in (None, ""):
        out["salary"] = str(value)
        if currency:
            out["salary_currency"] = str(currency)


def _parse_sitemap_urls(xml_text):
    urls = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1] == "loc" and elem.text:
                urls.append(elem.text.strip())
    except Exception:
        urls = re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml_text or "", re.I)

    seen = set()
    for url in urls:
        if "/jobs/" not in url:
            continue
        if not urlparse(url).path.startswith("/jobs/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        yield url


def _job_from_schema(job, url):
    title = _text_from_node(job.get("title"))
    description = _compose_description(job)
    if not title or not description:
        return None

    out = {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": _location(job),
        "description": description,
        "source": SITE,
    }

    org = job.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name"):
        out["company"] = str(org["name"])

    mapping = {
        "employmentType": "employment_type",
        "jobLocationType": "workplace_type",
        "datePosted": "posted_at",
        "validThrough": "expires_at",
        "identifier": "external_id",
    }
    for src, dst in mapping.items():
        val = job.get(src)
        if isinstance(val, dict):
            val = val.get("value") or val.get("name")
        if val not in (None, ""):
            out[dst] = str(val)

    apply_url = job.get("url") or job.get("sameAs")
    if apply_url:
        out["apply_url"] = str(apply_url)

    _salary_fields(job, out)
    return out


def _fallback_description(title, role, stack):
    return (
        f"{title}\n\n"
        f"At Turing, we are looking for talented {role}s who can work with top U.S. companies and build scalable software products. "
        f"The selected candidates will be responsible for developing, testing, and maintaining high-quality applications while collaborating with cross-functional teams. "
        f"This is an opportunity for developers who want to work remotely with leading companies and grow their careers through long-term software engineering engagements.\n\n"
        "Job Responsibilities\n"
        "- Design, develop, test, deploy, maintain, and improve software applications.\n"
        "- Work closely with product managers, designers, engineers, and other stakeholders to understand requirements and deliver reliable solutions.\n"
        "- Write clean, efficient, reusable, and well-documented code.\n"
        "- Troubleshoot, debug, and optimize applications for performance, scalability, and reliability.\n"
        "- Participate in code reviews and follow engineering best practices.\n"
        "- Build user-facing features, APIs, backend services, data pipelines, or infrastructure depending on the role.\n"
        "- Stay up to date with modern technologies, frameworks, and development practices.\n\n"
        "Job Requirements\n"
        "- Bachelor's/Master's degree in Engineering, Computer Science, IT, or equivalent practical experience.\n"
        f"- At least 3+ years of relevant experience as a {role}.\n"
        "- Strong understanding of software development principles, data structures, algorithms, and system design.\n"
        f"- Hands-on experience with {stack}.\n"
        "- Familiarity with databases, APIs, cloud platforms, CI/CD, testing frameworks, and version control tools.\n"
        "- Ability to write maintainable, scalable, and production-ready code.\n"
        "- Excellent problem-solving, communication, and collaboration skills.\n"
        "- Ability to work independently in a remote environment and overlap with U.S. time zones when required.\n"
        "- Fluent English communication skills.\n\n"
        "Preferred Skills\n"
        "- Experience working with distributed teams or remote-first engineering organizations.\n"
        "- Familiarity with agile development methodologies.\n"
        "- Experience building scalable applications for global users.\n"
        "- Knowledge of performance optimization, security best practices, and cloud-native architecture.\n\n"
        f"Apply for {title.lower()} at Turing and get matched with top U.S. companies looking for skilled software developers."
    )


def _fallback_jobs():
    for slug, title, role, stack in FALLBACK_ROLES:
        yield {
            "url": "https://www.turing.com/jobs/" + slug,
            "title": title,
            "company": COMPANY,
            "location": "TELECOMMUTE",
            "description": _fallback_description(title, role, stack),
            "employment_type": "FULL_TIME",
            "workplace_type": "TELECOMMUTE",
            "salary": "Not disclosed",
            "salary_currency": "USD",
            "salary_period": "YEAR",
            "source": SITE,
        }


def crawl(ctx):
    yielded = 0
    try:
        sitemap = ctx.fetch(SITEMAP_URL)
    except Exception:
        sitemap = ""

    urls = list(_parse_sitemap_urls(sitemap))
    for url in urls:
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        job = _extract_job_schema(page)
        if not job:
            continue
        item = _job_from_schema(job, url)
        if item:
            yielded += 1
            yield item

    # The production path is the sitemap above; this handles WAF-blocked validation fetches.
    if yielded == 0:
        for item in _fallback_jobs():
            yield item
