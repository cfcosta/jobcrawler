"""Snapshot / corpus shaping for the RLM generator (§4.2, §9).

The generator is kept *idiomatic* RLM by handing it a prefetched site snapshot
as the large input it reasons over in-REPL, using host tools only for follow-up
fetches. dspy injects ``corpus`` into the Pyodide REPL as a real variable by
serializing it as a Python literal, and **raises on any non-JSON type** — so the
corpus is an addressable dict of JSON primitives:

    {homepage, robots, sitemap, articles: [{url, title, text}, ...]}

Addressable parts (the model slices what it needs) beat one opaque blob — up to
8x fewer tokens. Pyodide ships no grep/sed, so ``REPL_HELPERS`` provides plain
navigation functions the generator prompt tells the model are available.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

_JSON_SCALARS = (str, int, float, bool, type(None))


def build_corpus(
    *,
    homepage: str,
    robots: str = "",
    sitemap: str = "",
    articles: Iterable[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Assemble the prefetched snapshot as an addressable, JSON-able dict (§9)."""
    corpus: dict[str, object] = {
        "homepage": homepage,
        "robots": robots,
        "sitemap": sitemap,
        "articles": [_article(a) for a in (articles or [])],
    }
    ensure_json_primitive(corpus)
    return corpus


def _article(article: Mapping[str, object]) -> dict[str, str]:
    return {
        "url": str(article.get("url") or ""),
        "title": str(article.get("title") or ""),
        "text": str(article.get("text") or ""),
    }


def ensure_json_primitive(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is built only of JSON primitives.

    Mirrors dspy's in-sandbox serializer (which raises ``CodeInterpreterError``
    on a non-JSON type), so a bad corpus fails here with a clear message rather
    than deep inside the RLM REPL bridge.
    """
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"corpus dict key must be str, got {type(k).__name__}")
            ensure_json_primitive(v)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            ensure_json_primitive(item)
        return
    raise ValueError(f"corpus holds a non-JSON type: {type(value).__name__}")


# Navigation helpers the generator prompt advertises (Pyodide lacks grep/sed).
# Plain Python so the model can call them on corpus parts inside the REPL.
REPL_HELPERS = '''
def grep(pattern, text, *, flags=0):
    """Return lines of `text` matching the regex `pattern`."""
    import re
    rx = re.compile(pattern, flags)
    return [line for line in text.splitlines() if rx.search(line)]


def slice_text(text, start, end):
    """Return `text[start:end]` — a window into a large corpus part."""
    return text[start:end]
'''
