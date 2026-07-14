
import json

SITE = "https://www.group.sinch.com/open-positions/"
EXTRA_HOSTS = []


def _secondary_location(value):
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None

    parts = []
    for key in ("city", "stateCode", "region", "zipCode", "countryName"):
        part = value.get(key)
        if part and part not in parts:
            parts.append(str(part))
    if not parts and value.get("countryCode"):
        parts.append(str(value["countryCode"]))
    return ", ".join(parts) if parts else None


def _description(item):
    primary = item.get("location") or {}
    lines = []

    category = item.get("jobCategory")
    if category:
        lines.append("Job category: " + str(category))

    department = item.get("department")
    if department:
        lines.append("Department: " + str(department))

    hierarchy = []
    for level in item.get("departmentHierarchy") or []:
        if isinstance(level, dict) and level.get("name"):
            hierarchy.append(str(level["name"]))
    if hierarchy:
        lines.append("Department hierarchy: " + " > ".join(hierarchy))

    if primary.get("locationStr"):
        lines.append("Location: " + str(primary["locationStr"]))
    if primary.get("workplaceType"):
        lines.append("Workplace type: " + str(primary["workplaceType"]))

    secondary = []
    for value in item.get("locations") or []:
        display = _secondary_location(value)
        if display:
            secondary.append(display)
    if secondary:
        lines.append("Additional locations: " + "; ".join(secondary))

    salary = item.get("salary") or {}
    salary_from = salary.get("salaryFrom")
    salary_to = salary.get("salaryTo")
    currency = salary.get("salaryCurrency")
    if salary_from is not None and salary_to is not None and currency:
        lines.append(
            "Salary range: "
            + str(salary_from)
            + "-"
            + str(salary_to)
            + " "
            + str(currency)
        )

    if item.get("createdAt"):
        lines.append("Published: " + str(item["createdAt"]))
    if item.get("_id"):
        lines.append("Job ID: " + str(item["_id"]))

    return "\n".join(lines)


def crawl(ctx):
    items = json.loads(ctx.fetch(SITE))
    for item in items:
        if item.get("status") != "Posted":
            continue

        primary = item.get("location") or {}
        title = item.get("title")
        location = primary.get("locationStr")
        url = item.get("url")
        if not title or not location or not url:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Sinch",
            "location": location,
            "description": _description(item),
            "source": SITE,
        }

        department = item.get("department") or item.get("jobCategory")
        if department:
            job["department"] = department
        if item.get("createdAt"):
            job["posted_at"] = item["createdAt"]
        if item.get("_id"):
            job["external_id"] = str(item["_id"])
        job["apply_url"] = url

        workplace = primary.get("workplaceType")
        if workplace:
            job["workplace_type"] = workplace

        structured_locations = []
        for value in item.get("locations") or []:
            display = _secondary_location(value)
            if display:
                structured_locations.append(display)
        if structured_locations:
            job["locations"] = structured_locations

        salary = item.get("salary") or {}
        if salary.get("salaryFrom") is not None:
            job["salary_min"] = salary["salaryFrom"]
        if salary.get("salaryTo") is not None:
            job["salary_max"] = salary["salaryTo"]
        if salary.get("salaryCurrency"):
            job["salary_currency"] = salary["salaryCurrency"]

        yield job
