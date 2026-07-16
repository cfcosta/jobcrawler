
import json

SITE = "https://jobs.modem.dev/"
EXTRA_HOSTS = []

COLLECTION_ID = "3022c584-2de0-800d-b56a-000b87d4aee8"
VIEW_ID = "3022c584-2de0-80d0-aa0b-000cb6894802"
SPACE_ID = "56375bb1-2041-440f-b314-ae4c1824ce63"


def _unwrap(entry):
    value = entry
    while isinstance(value, dict) and isinstance(value.get("value"), dict):
        value = value["value"]
    return value if isinstance(value, dict) else {}


def _text(rich_text):
    if not isinstance(rich_text, list):
        return None
    parts = []
    for segment in rich_text:
        if isinstance(segment, list) and segment and isinstance(segment[0], str):
            parts.append(segment[0])
    value = "".join(parts).strip()
    return value or None


def _property(row, key):
    return _text((row.get("properties") or {}).get(key))


def _description(page_id, blocks):
    page = _unwrap(blocks.get(page_id, {}))
    lines = []

    def visit(block_id):
        block = _unwrap(blocks.get(block_id, {}))
        if not block or block.get("alive") is False:
            return

        value = _text((block.get("properties") or {}).get("title"))
        if value:
            if block.get("type") in ("bulleted_list", "numbered_list"):
                value = "- " + value
            lines.append(value)

        for child_id in block.get("content") or []:
            visit(child_id)

    for child_id in page.get("content") or []:
        visit(child_id)

    return "\n\n".join(lines).strip()


def crawl(ctx):
    query = json.loads(ctx.fetch(
        SITE + "api/v3/queryCollection?src=initial_load",
        method="POST",
        headers={"Content-Type": "application/json"},
        json={
            "collection": {
                "id": COLLECTION_ID,
                "spaceId": SPACE_ID,
            },
            "collectionView": {
                "id": VIEW_ID,
                "spaceId": SPACE_ID,
            },
            "loader": {
                "type": "reducer",
                "reducers": {
                    "collection_group_results": {
                        "type": "results",
                        "limit": 50,
                    }
                },
                "searchQuery": "",
                "userTimeZone": "UTC",
            },
        },
    ))

    block_ids = (
        query.get("result", {})
        .get("reducerResults", {})
        .get("collection_group_results", {})
        .get("blockIds", [])
    )
    rows = (query.get("recordMap") or {}).get("block") or {}

    for block_id in block_ids:
        row = _unwrap(rows.get(block_id, {}))
        if row.get("alive") is False or _property(row, "mCSG") != "Open":
            continue

        title = _property(row, "title")
        location = _property(row, "NpAY")
        if not title or not location:
            continue

        page_id = row.get("id") or block_id
        chunk = json.loads(ctx.fetch(
            SITE + "api/v3/loadCachedPageChunkV2",
            method="POST",
            headers={"Content-Type": "application/json"},
            json={
                "page": {"id": page_id},
                "limit": 100,
                "cursor": {"stack": []},
                "chunkNumber": 0,
                "verticalColumns": False,
            },
        ))
        page_blocks = (chunk.get("recordMap") or {}).get("block") or {}
        description = _description(page_id, page_blocks)
        if not description:
            continue

        locations = [part.strip() for part in location.split(",") if part.strip()]
        yield {
            "url": SITE + page_id.replace("-", ""),
            "title": title,
            "company": "Modem",
            "location": location,
            "locations": locations,
            "description": description,
            "external_id": page_id,
            "source": SITE,
        }
