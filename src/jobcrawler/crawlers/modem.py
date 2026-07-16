
import json

SITE = "https://jobs.modem.dev/"
EXTRA_HOSTS = []

_COLLECTION_ID = "3022c584-2de0-800d-b56a-000b87d4aee8"
_VIEW_ID = "3022c584-2de0-80d0-aa0b-000cb6894802"
_SPACE_ID = "56375bb1-2041-440f-b314-ae4c1824ce63"


def _value(wrapper):
    value = wrapper or {}
    while isinstance(value, dict) and isinstance(value.get("value"), dict):
        value = value["value"]
    return value


def _rich_text(segments):
    if not isinstance(segments, list):
        return None

    parts = []
    for segment in segments:
        if isinstance(segment, list) and segment and isinstance(segment[0], str):
            parts.append(segment[0])

    text = "".join(parts).strip()
    return text or None


def _links(segments):
    result = []
    if not isinstance(segments, list):
        return result

    for segment in segments:
        if not isinstance(segment, list) or len(segment) < 2:
            continue
        annotations = segment[1]
        if not isinstance(annotations, list):
            continue
        for annotation in annotations:
            if (
                isinstance(annotation, list)
                and len(annotation) > 1
                and annotation[0] == "a"
                and isinstance(annotation[1], str)
            ):
                result.append(annotation[1])
    return result


def _render_blocks(block_ids, blocks, lines, apply_links):
    for block_id in block_ids or []:
        block = _value(blocks.get(block_id))
        properties = block.get("properties") or {}
        segments = properties.get("title")
        text = _rich_text(segments)
        block_type = block.get("type")

        if text:
            if block_type == "bulleted_list":
                lines.append("- " + text)
            elif block_type == "numbered_list":
                lines.append("1. " + text)
            else:
                lines.append(text)
            apply_links.extend(_links(segments))
        elif lines and lines[-1] != "":
            lines.append("")

        _render_blocks(block.get("content"), blocks, lines, apply_links)


def _post(ctx, url, body):
    response = ctx.fetch(
        url,
        method="POST",
        json=body,
    )
    return json.loads(response)


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
                    "limit": 50,
                }
            },
            "searchQuery": "",
            "userTimeZone": "UTC",
        },
    }

    query_data = _post(
        ctx,
        SITE + "api/v3/queryCollection?src=initial_load",
        query_payload,
    )

    result = query_data.get("result") or {}
    reducer_results = result.get("reducerResults") or {}
    group = reducer_results.get("collection_group_results") or {}
    row_ids = group.get("blockIds") or []
    query_blocks = (query_data.get("recordMap") or {}).get("block") or {}

    for row_id in row_ids:
        row = _value(query_blocks.get(row_id))
        properties = row.get("properties") or {}

        if _rich_text(properties.get("mCSG")) != "Open":
            continue

        title = _rich_text(properties.get("title"))
        location = _rich_text(properties.get("NpAY"))
        if not title or not location:
            continue

        chunk_payload = {
            "page": {"id": row_id},
            "limit": 100,
            "cursor": {"stack": []},
            "chunkNumber": 0,
            "verticalColumns": False,
        }
        chunk_data = _post(
            ctx,
            SITE + "api/v3/loadCachedPageChunkV2",
            chunk_payload,
        )

        blocks = (chunk_data.get("recordMap") or {}).get("block") or {}
        page = _value(blocks.get(row_id))

        lines = []
        apply_links = []
        _render_blocks(page.get("content"), blocks, lines, apply_links)

        while lines and not lines[-1]:
            lines.pop()

        description = "\n".join(lines).strip()
        if not description:
            continue

        locations = [
            item.strip()
            for item in location.split(",")
            if item.strip()
        ]

        fields = {
            "url": SITE + row_id.replace("-", ""),
            "title": title,
            "company": "Modem",
            "location": location,
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": row_id,
        }
        if apply_links:
            fields["apply_url"] = apply_links[-1]

        yield fields
