"""Tests for untrusted-XML hardening (§8).

Feed/sitemap parsing consumes attacker-influenced XML on *every committed crawl*,
so the TCB disables external-entity resolution and entity expansion (XXE /
billion-laughs) before any crawler code runs. ``harden_xml`` is the primitive;
the runner and the sandbox entry call it. These exercise it against stdlib
``xml.etree`` (the canonical demonstration; ``defuse_stdlib`` also covers the
``xml.sax`` backend ``feedparser`` rides on).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from defusedxml.common import DefusedXmlException

from jobcrawler.runtime.hardening import harden_xml

# A SYSTEM external-entity read (the classic file-exfiltration XXE).
_XXE = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    "<foo>&xxe;</foo>"
)

# Nested internal entities — "billion laughs" RAM exhaustion.
_BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE lolz [\n"
    '  <!ENTITY lol "lol">\n'
    '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">\n'
    '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
    "]>\n"
    "<lolz>&lol3;</lolz>"
)


def test_harden_xml_blocks_external_entities() -> None:
    harden_xml()
    with pytest.raises(DefusedXmlException):
        ET.fromstring(_XXE)


def test_harden_xml_blocks_entity_expansion() -> None:
    harden_xml()
    with pytest.raises(DefusedXmlException):
        ET.fromstring(_BILLION_LAUGHS)


def test_harden_xml_still_parses_benign_feed_xml() -> None:
    harden_xml()
    root = ET.fromstring("<rss><channel><item><title>Hi</title></item></channel></rss>")
    title = root.find("channel/item/title")
    assert title is not None and title.text == "Hi"


def test_harden_xml_is_idempotent() -> None:
    harden_xml()
    harden_xml()  # second call must not raise or undo the protection
    with pytest.raises(DefusedXmlException):
        ET.fromstring(_XXE)
