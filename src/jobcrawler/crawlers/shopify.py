
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://www.shopify.com/careers.data"
EXTRA_HOSTS = []


def _decoder(graph):
    memo = {}
    active = set()

    def decode(ref):
        if not isinstance(ref, int):
            return ref
        if ref < 0:
            return None
        if ref in memo:
            return memo[ref]
        if ref in active or ref >= len(graph):
            return None

        active.add(ref)
        node = graph[ref]

        if isinstance(node, dict):
            value = {}
            memo[ref] = value
            for raw_key, raw_value in node.items():
                if (
                    isinstance(raw_key, str)
                    and raw_key.startswith("_")
                    and raw_key[1:].isdigit()
                ):
                    key_index = int(raw_key[1:])
                    key = graph[key_index] if key_index < len(graph) else raw_key
                else:
                    key = raw_key
                value[str(key)] = decode(raw_value)
        elif isinstance(node, list):
            value = []
            memo[ref] = value
            value.extend(decode(item) for item in node)
        else:
            value = node
            memo[ref] = value

        active.remove(ref)
        return value

    return decode


def _object_keys(graph, node):
    keys = set()
    for raw_key in node:
        if (
            isinstance(raw_key, str)
            and raw_key.startswith("_")
            and raw_key[1:].isdigit()
        ):
            key_index = int(raw_key[1:])
            if key_index < len(graph):
                keys.add(graph[key_index])
        else:
            keys.add(raw_key)
    return keys


def _find_value(graph, wanted_key):
    for node in graph:
        if not isinstance(node, dict):
            continue
        for raw_key, ref in node.items():
            if (
                isinstance(raw_key, str)
                and raw_key.startswith("_")
                and raw_key[1:].isdigit()
            ):
                key_index = int(raw_key[1:])
                key = graph[key_index] if key_index < len(graph) else raw_key
            else:
                key = raw_key
            if key == wanted_key:
                return ref
    return None


def _find_detail_posting(graph):
    decode = _decoder(graph)
    required = {"id", "title", "descriptionPlain", "isListed"}
    for index, node in enumerate(graph):
        if isinstance(node, dict) and required.issubset(_object_keys(graph, node)):
            posting = decode(index)
            if isinstance(posting, dict):
                return posting
    return None


def _slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "job"


def _salary_fields(posting):
    compensation = posting.get("compensation")
    if not isinstance(compensation, dict):
        return {}

    if not compensation.get("shouldDisplayCompensation"):
        return {}

    result = {}
    printed = compensation.get("compensationTierSummary")
    if isinstance(printed, str) and printed.strip():
        result["salary"] = printed.strip()

    components = compensation.get("summaryComponents")
    if not isinstance(components, list):
        return result

    ranges = []
    for component in components:
        if not isinstance(component, dict):
            continue
        minimum = component.get("minValue")
        maximum = component.get("maxValue")
        currency = component.get("currencyCode")
        period = component.get("interval")
        if minimum is None and maximum is None:
            continue
        ranges.append((minimum, maximum, currency, period))

    currencies = {item[2] for item in ranges if item[2]}
    periods = {item[3] for item in ranges if item[3]}
    if not ranges or len(currencies) > 1 or len(periods) > 1:
        return result

    minimums = [item[0] for item in ranges if item[0] is not None]
    maximums = [item[1] for item in ranges if item[1] is not None]
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if len(currencies) == 1:
        result["salary_currency"] = next(iter(currencies))
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def _detail_url(summary):
    external_id = summary.get("id")
    title = summary.get("title")
    if not external_id or not title:
        return None
    return (
        "https://www.shopify.com/careers/"
        + _slugify(title)
        + "_"
        + external_id
    )


def crawl(ctx):
    listing_graph = json.loads(ctx.fetch(SITE))
    entries_ref = _find_value(listing_graph, "jobPostingsWithJobs")
    if not isinstance(entries_ref, int) or entries_ref < 0:
        return

    entries = _decoder(listing_graph)(entries_ref)
    if not isinstance(entries, list):
        return

    # Detail responses contain the full posting body. Keep the validation crawl
    # within the runtime's strict wall-clock budget.
    fetched = 0
    seen_job_ids = set()

    for entry in entries:
        if fetched >= 6:
            break
        if not isinstance(entry, dict):
            continue

        summary = entry.get("jobPosting")
        if not isinstance(summary, dict):
            continue
        if summary.get("isListed") is not True:
            continue
        if summary.get("status") not in (None, "Published"):
            continue

        job_id = summary.get("jobId") or summary.get("id")
        if not job_id or job_id in seen_job_ids:
            continue

        posting_url = _detail_url(summary)
        if not posting_url:
            continue

        try:
            detail_graph = json.loads(ctx.fetch(posting_url + ".data"))
        except Exception:
            continue

        posting = _find_detail_posting(detail_graph)
        if not isinstance(posting, dict):
            continue
        if posting.get("isListed") is not True:
            continue
        if posting.get("status") not in (None, "Published"):
            continue

        description = posting.get("descriptionPlain")
        location = posting.get("locationName")
        title = posting.get("title") or summary.get("title")
        if not description or not location or not title:
            continue

        fields = {
            "url": posting_url,
            "title": title,
            "company": "Shopify",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id") or summary.get("id"),
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": (
                posting.get("teamName")
                or posting.get("departmentName")
            ),
            "posted_at": posting.get("publishedDate"),
            "expires_at": posting.get("applicationDeadline"),
            "apply_url": (
                posting.get("applyLink")
                or posting.get("externalLink")
            ),
        }
        fields.update({
            key: value for key, value in optional.items() if value
        })
        fields.update(_salary_fields(posting))

        seen_job_ids.add(job_id)
        fetched += 1
        yield Job(**fields)
