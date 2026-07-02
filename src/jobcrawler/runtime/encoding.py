"""The §4.5 text-encoding ladder — turn fetched bytes into UTF-8 ``str``.

``ctx`` decodes every response centrally so per-site crawlers never guess a
charset (a wrong guess shows up downstream as replacement-char mojibake, which
gate-2 rejects, §6). The ladder, highest-confidence signal first:

1. **HTTP ``Content-Type`` charset** — the server's explicit declaration.
2. **Byte-order mark** — a definitive in-band marker (UTF-8/16/32).
3. **``<meta charset>`` / ``<meta http-equiv>``** — the document's own
   declaration, prescanned in the first ~2KB (bounded, WHATWG-style).
4. **``charset-normalizer``** — statistical detection for legacy pages that
   declare nothing.

Each rung is tried only if the ones above produced no usable codec; a declared
codec that fails to decode the bytes falls through rather than yielding mojibake.
"""

from __future__ import annotations

import codecs
import re

from charset_normalizer import from_bytes

# A charset declared in a <meta> tag, either as the HTML5 ``charset="..."``
# attribute or inside an ``http-equiv`` ``content="...; charset=..."`` value.
_META_CHARSET = re.compile(
    rb"<meta[^>]+?charset[\"'\s=]*([a-z0-9][a-z0-9._\-]*)", re.IGNORECASE
)
# Bound only the <meta charset> regex search window (WHATWG-style); this is NOT a
# body cap — ``resolve_text`` still decodes the full ``content``, so multi-MB ATS
# JSON boards come back whole, never truncated by the charset sniff.
_PRESCAN_BYTES = 2048

_UTF32_BOMS = (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def resolve_text(content: bytes, http_charset: str | None) -> str:
    """Decode ``content`` to ``str`` via the §4.5 ladder, never raising."""
    # rung 1: the server's declared charset wins when present and usable.
    if http_charset:
        text = _try_decode(content, http_charset)
        if text is not None:
            return text
    # rung 2a: a BOM is a definitive byte-level marker.
    bom_codec = _sniff_bom(content)
    if bom_codec is not None:
        text = _try_decode(content, bom_codec)
        if text is not None:
            return text
    # rung 2b: the document's own <meta> declaration (bounded prescan).
    meta = _sniff_meta_charset(content)
    if meta is not None:
        text = _try_decode(content, meta)
        if text is not None:
            return text
    # rung 3: statistical fallback for pages that declare nothing.
    return _detect(content)


def http_charset_from_content_type(content_type: str | None) -> str | None:
    """Pull the ``charset`` parameter out of a ``Content-Type`` header value."""
    if not content_type:
        return None
    for param in content_type.split(";")[1:]:
        key, sep, value = param.partition("=")
        if sep and key.strip().lower() == "charset":
            return value.strip().strip("\"'") or None
    return None


def _try_decode(content: bytes, charset: str) -> str | None:
    """Decode with ``charset`` if it names a real codec and the bytes are valid."""
    codec = _normalize_codec(charset)
    if codec is None:
        return None
    try:
        return content.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None


def _normalize_codec(name: str) -> str | None:
    try:
        return codecs.lookup(name.strip()).name
    except LookupError:
        return None


def _sniff_bom(content: bytes) -> str | None:
    # UTF-32 first: its LE BOM starts with the UTF-16 LE BOM, so order matters.
    if content.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"  # the -sig codec consumes the BOM
    if content.startswith(_UTF32_BOMS):
        return "utf-32"  # endianness read from the BOM, which is stripped
    if content.startswith(_UTF16_BOMS):
        return "utf-16"
    return None


def _sniff_meta_charset(content: bytes) -> str | None:
    match = _META_CHARSET.search(content[:_PRESCAN_BYTES])
    if match is None:
        return None
    return match.group(1).decode("ascii", errors="ignore")


def _detect(content: bytes) -> str:
    best = from_bytes(content).best()
    if best is not None:
        return str(best)
    return content.decode("utf-8", errors="replace")
