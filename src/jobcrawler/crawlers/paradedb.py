
import json

SITE = "https://paradedb.notion.site/"
EXTRA_HOSTS = []

_COLLECTION_ID = "15a73338-fb90-4d78-862d-ce5ffd16c285"
_SPACE_ID = "a48ca041-a00e-4021-abae-8156f5071fc3"
_VIEW_ID = "534341d4-adb1-4810-947b-0fd9b979ba0f"


def _unwrap(entry):
    value = entry.get("value", entry)
    if isinstance(value, dict) and isinstance(value.get("value"), dict):
        return value["value"]
    return value


def _plain_rich_text(parts):
    return "".join(
        part[0]
        for part in (parts or [])
        if part and isinstance(part[0], str)
    ).strip()


def _property_text(properties, prop_id):
    return _plain_rich_text((properties or {}).get(prop_id))


def _render_block(block_id, blocks, lines):
    entry = blocks.get(block_id)
    if not entry:
        return

    block = _unwrap(entry)
    block_type = block.get("type")
    text = _plain_rich_text((block.get("properties") or {}).get("title"))

    if text:
        if block_type in ("bulleted_list", "numbered_list", "to_do"):
            lines.append("- " + text)
        else:
            lines.append(text)
    elif block_type == "divider":
        lines.append("---")

    for child_id in block.get("content") or []:
        _render_block(child_id, blocks, lines)


def _fetch_json(ctx, url, payload):
    response = ctx.fetch(url, method="POST", json=payload)
    if isinstance(response, str):
        return json.loads(response)
    try:
        return response.json()
    except Exception:
        return json.loads(response.text)


def crawl(ctx):
    query_payload = {
        "collection": {
            "id": _COLLECTION_ID,
            "spaceId": _SPACE_ID,
        },
        "collectionView": {
            "id": _VIEW_ID,
            "spaceId": _SPACE_ID,
        },
        "loader": {
            "type": "reducer",
            "reducers": {
                "collection_group_results": {
                    "type": "results",
                    "limit": 100,
                }
            },
            "searchQuery": "",
            "userTimeZone": "America/Los_Angeles",
        },
    }
    data = _fetch_json(ctx, SITE + "api/v3/queryCollection", query_payload)
    record_map = data["recordMap"]

    collection = _unwrap(record_map["collection"][_COLLECTION_ID])
    schema = collection.get("schema") or {}
    property_ids = {
        definition.get("name"): prop_id
        for prop_id, definition in schema.items()
    }

    results = data["result"]["reducerResults"]["collection_group_results"]
    for page_id in results.get("blockIds") or []:
        row = _unwrap(record_map["block"][page_id])
        properties = row.get("properties") or {}

        title = _property_text(properties, property_ids.get("Name", "title"))
        department = _property_text(
            properties, property_ids.get("Department")
        )
        if not title or department == "Archived":
            continue

        location = _property_text(properties, property_ids.get("Location"))
        employment_type = _property_text(
            properties, property_ids.get("Employment Type")
        )

        page_data = _fetch_json(
            ctx,
            SITE + "api/v3/loadPageChunk",
            {
                "pageId": page_id,
                "limit": 100,
                "cursor": {"stack": []},
                "chunkNumber": 0,
                "verticalColumns": False,
            },
        )
        blocks = page_data["recordMap"]["block"]
        page = _unwrap(blocks[page_id])
        lines = []
        for child_id in page.get("content") or []:
            _render_block(child_id, blocks, lines)
        description = "\n\n".join(lines).strip()

        locations = [
            item.strip() for item in location.split(",") if item.strip()
        ]
        job_url = SITE + page_id.replace("-", "")

        kwargs = {
            "url": job_url,
            "title": title,
            "company": "ParadeDB",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": page_id,
        }
        if locations:
            kwargs["locations"] = locations
        if department:
            kwargs["department"] = department
        if employment_type:
            kwargs["employment_type"] = employment_type

        yield kwargs
