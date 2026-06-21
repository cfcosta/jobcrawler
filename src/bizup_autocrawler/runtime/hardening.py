"""Untrusted-XML hardening for the hot path (§8).

Feed/sitemap parsing consumes **attacker-influenced** XML on *every committed
crawl* — a malicious feed can mount XXE (external-entity file/SSRF reads) or a
billion-laughs entity-expansion RAM bomb. ``harden_xml`` disables external-entity
resolution and entity expansion process-wide *before* any crawler code runs, in
both execution paths (the §4.4 sandbox subprocess and the in-process committed
crawl). It is part of the **pinned-image TCB**: model-generated code never
parameterizes or relaxes it.

``defuse_stdlib`` patches the stdlib parsers (``xml.etree``, ``xml.sax`` —
which ``feedparser`` rides on — ``xml.dom.minidom``/``pulldom``); ``lxml``, if
ever installed, is hardened by swapping its default parser to one with
``resolve_entities``/``load_dtd`` off and ``no_network`` on. The call is
idempotent so the per-crawl and per-subprocess entry points can both invoke it
cheaply.
"""

from __future__ import annotations

from defusedxml import defuse_stdlib

_hardened = False


def harden_xml() -> None:
    """Disable XXE / entity expansion across every XML parser in this process."""
    global _hardened
    if _hardened:
        return
    defuse_stdlib()  # xml.etree / xml.sax (feedparser) / xml.dom
    _harden_lxml()
    _hardened = True


def _harden_lxml() -> None:
    """Swap lxml's default parser for a hardened one, if lxml is installed."""
    try:
        from lxml import etree  # ty: ignore[unresolved-import]
    except ImportError:
        return  # lxml is not a v1 dependency; nothing to harden
    etree.set_default_parser(
        etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    )
