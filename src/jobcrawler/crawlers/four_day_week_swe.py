
import datetime
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://4dayweek.io/software-engineering-jobs"
EXTRA_HOSTS = []

_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\s*\[\s*1\s*,\s*("(?:\\.|[^"\\])*")\s*\]\s*\)',
    re.S,
)


def _flight_text(page):
    chunks = []
    for encoded in _PUSH_RE.findall(page):
        try:
            chunks.append(json.loads(encoded))
        except (TypeError, ValueError):
            continue
    return "".join(chunks)


def _job_objects(page):
    flight = _flight_text(page)
    decoder = json.JSONDecoder()
    jobs = []
    seen = set()
    position = 0

    while True:
        marker = flight.find('"company_name"', position)
        if marker < 0:
            break
        position = marker + len('"company_name"')

        # The listing record starts with id and title; nested id objects occur
        # later in the record, after company_name.
        start = flight.rfind('{"id":', 0, marker)
        if start < 0:
            continue
        try:
            record, _ = decoder.raw_decode(flight[start:])
        except (TypeError, ValueError):
            continue

        external_id = record.get("id") if isinstance(record, dict) else None
        if (
            external_id
            and external_id not in seen
            and record.get("company_name")
            and record.get("slug")
        ):
            seen.add(external_id)
            jobs.append(record)

    return jobs


def _location_name(location):
    if not isinstance(location, dict):
        return str(location).strip()

    parts = []
    for key in ("city", "state", "country"):
        value = location.get(key)
        if value and value not in parts:
            parts.append(str(value))
    if not parts and location.get("continent"):
        parts.append(str(location["continent"]))
    return ", ".join(parts)


def _description(record, locations):
    lines = [
        record["title"],
        "Company: " + record["company_name"],
        "Location: " + "; ".join(locations),
    ]

    labels = (
        ("work_arrangement", "Work arrangement"),
        ("category", "Category"),
        ("level", "Level"),
        ("schedule_type", "Schedule"),
    )
    for key, label in labels:
        value = record.get(key)
        if value:
            lines.append(label + ": " + str(value))

    timezones = record.get("timezones") or []
    if timezones:
        lines.append("Time zones: " + ", ".join(str(v) for v in timezones))

    stack = [
        str(item.get("name"))
        for item in (record.get("stack") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    if stack:
        lines.append("Technology stack: " + ", ".join(stack))

    location_details = []
    for item in record.get("locations") or []:
        if not isinstance(item, dict):
            continue
        values = []
        for key in ("city", "state", "country", "continent", "work_arrangement"):
            if item.get(key):
                values.append(key.replace("_", " ").title() + ": " + str(item[key]))
        if values:
            location_details.append("; ".join(values))
    if location_details:
        lines.append("Locations:")
        lines.extend(location_details)

    score = record.get("work_life_score")
    if score is not None:
        lines.append("Work-life score: " + str(score))

    lower = record.get("salary_lower")
    upper = record.get("salary_upper")
    currency = record.get("salary_currency")
    period = record.get("salary_period")
    if lower is not None or upper is not None:
        bounds = []
        if lower is not None:
            bounds.append(str(lower / 100))
        if upper is not None:
            bounds.append(str(upper / 100))
        salary_text = " - ".join(bounds)
        if currency:
            salary_text += " " + str(currency)
        if period:
            salary_text += " per " + str(period)
        lines.append("Salary: " + salary_text)

    return "\n".join(lines)


def crawl(ctx):
    seen = set()
    page_number = 1

    while True:
        url = SITE if page_number == 1 else SITE + "?page=" + str(page_number)
        page = ctx.fetch(url)
        records = _job_objects(page)

        for record in records:
            external_id = record.get("id")
            if not external_id or external_id in seen or record.get("is_expired"):
                continue
            seen.add(external_id)

            locations = [
                _location_name(item)
                for item in (record.get("locations") or [])
            ]
            locations = [value for value in locations if value]
            if not locations:
                locations = [str(record.get("work_arrangement") or "Unspecified")]

            kwargs = {
                "url": "https://4dayweek.io/job/" + record["slug"],
                "title": record["title"],
                "company": record["company_name"],
                "location": locations[0],
                "locations": locations,
                "description": _description(record, locations),
                "source": SITE,
                "external_id": external_id,
            }

            if record.get("work_arrangement"):
                kwargs["workplace_type"] = record["work_arrangement"]
            if record.get("category"):
                kwargs["department"] = record["category"]
            if record.get("posted") is not None:
                kwargs["posted_at"] = datetime.datetime.fromtimestamp(
                    record["posted"], datetime.timezone.utc
                ).isoformat()

            if record.get("salary_lower") is not None:
                kwargs["salary_min"] = record["salary_lower"] / 100
            if record.get("salary_upper") is not None:
                kwargs["salary_max"] = record["salary_upper"] / 100
            if record.get("salary_currency"):
                kwargs["salary_currency"] = record["salary_currency"]
            if record.get("salary_period"):
                kwargs["salary_period"] = record["salary_period"]

            yield Job(**kwargs)

        if len(records) < 50:
            break
        page_number += 1
