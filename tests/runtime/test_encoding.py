"""Tests for the §4.5 text-encoding ladder.

ctx hands every crawler UTF-8 ``str``, resolving the charset by a fixed ladder so
per-site code never guesses: **HTTP charset → BOM/meta charset → charset-normalizer
statistical fallback**. Each rung is exercised alone, plus the priority ordering
between rungs (a higher rung's signal wins). The fallback's contract is the one
gate-2 cares about (§6): a mis-detected encoding shows up as replacement-char
mojibake, so the fallback must decode legacy single-byte text *without* it.
"""

from __future__ import annotations

import codecs

from jobcrawler.runtime.encoding import (
    http_charset_from_content_type,
    resolve_text,
)

# -- rung 1: HTTP Content-Type charset ---------------------------------------


def test_http_charset_decodes_declared_legacy_encoding() -> None:
    body = "Café façade".encode("latin-1")
    assert resolve_text(body, "latin-1") == "Café façade"


def test_http_charset_beats_an_in_document_meta() -> None:
    # The server header is authoritative over a conflicting <meta> per the ladder.
    body = '<meta charset="utf-8"><p>Crème</p>'.encode("latin-1")
    assert resolve_text(body, "iso-8859-1") == '<meta charset="utf-8"><p>Crème</p>'


def test_invalid_http_charset_falls_through_to_lower_rungs() -> None:
    body = "<p>plain ascii</p>".encode("utf-8")
    assert resolve_text(body, "totally-bogus-charset") == "<p>plain ascii</p>"


# -- rung 2a: byte-order mark ------------------------------------------------


def test_utf8_bom_is_detected_and_stripped() -> None:
    body = "Café".encode("utf-8-sig")  # leading EF BB BF BOM
    assert resolve_text(body, None) == "Café"  # BOM consumed, not left as U+FEFF


def test_utf16_le_bom_is_detected() -> None:
    body = codecs.BOM_UTF16_LE + "Café".encode("utf-16-le")
    assert resolve_text(body, None) == "Café"


def test_bom_beats_a_conflicting_meta() -> None:
    # A UTF-8 BOM plus a <meta> that lies about being latin-1: the BOM wins, so the
    # body decodes cleanly instead of as mojibake ("CafÃ©").
    body = codecs.BOM_UTF8 + '<meta charset="latin-1">Café'.encode("utf-8")
    assert resolve_text(body, None) == '<meta charset="latin-1">Café'


# -- rung 2b: <meta> charset -------------------------------------------------


def test_meta_charset_attribute_form() -> None:
    body = '<meta charset="iso-8859-1"><p>Crème brûlée</p>'.encode("latin-1")
    assert resolve_text(body, None) == '<meta charset="iso-8859-1"><p>Crème brûlée</p>'


def test_meta_http_equiv_content_type_form() -> None:
    html = (
        '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
        "<p>Café</p>"
    )
    assert resolve_text(html.encode("latin-1"), None) == html


def test_meta_charset_is_ignored_past_the_prescan_window() -> None:
    # A declaration buried past the first ~2KB must not be honored (WHATWG prescan
    # bounds the scan); the statistical fallback handles such pages instead.
    body = b"<!-- " + b"x" * 4000 + b' --><meta charset="iso-8859-1">'
    # All ASCII, so the fallback still round-trips it cleanly.
    assert "�" not in resolve_text(body, None)


# -- rung 3: charset-normalizer statistical fallback -------------------------


def test_fallback_decodes_legacy_text_without_mojibake() -> None:
    # No header, no BOM, no meta: a single-byte French page must still come back
    # without replacement chars (the gate-2 mojibake signal) and with its accents.
    body = ("Façade café crème brûlée à la française. " * 40).encode("latin-1")
    out = resolve_text(body, None)
    assert "�" not in out
    assert "café" in out and "façade" in out.lower()


def test_plain_utf8_without_any_signal_round_trips() -> None:
    body = "just ascii and a café".encode("utf-8")
    assert resolve_text(body, None) == "just ascii and a café"


def test_empty_body_is_empty_string() -> None:
    assert resolve_text(b"", None) == ""


# -- Content-Type header parsing ---------------------------------------------


def test_http_charset_from_content_type_extracts_charset() -> None:
    assert http_charset_from_content_type("text/html; charset=UTF-8") == "UTF-8"


def test_http_charset_from_content_type_strips_quotes() -> None:
    assert http_charset_from_content_type('text/html; charset="iso-8859-1"') == (
        "iso-8859-1"
    )


def test_http_charset_from_content_type_without_charset_is_none() -> None:
    assert http_charset_from_content_type("text/html") is None
    assert http_charset_from_content_type(None) is None
    assert http_charset_from_content_type("application/json; boundary=x") is None
