"""Tests for the §8 restricted exec environment (the runtime half of the sandbox).

These pin the defense that does *not* trust the AST gate: even if dangerous code
slipped past static analysis, the ``__builtins__`` a crawler execs under has no
``eval``/``exec``/``open``/``compile``/``getattr`` and only a guarded ``__import__``
that admits the same module allowlist the gate enforces. Legitimate crawler imports
(``re``/``json``/``collections.abc``/parsers/the contract) and value builtins must
still work, or every committed crawler would break.
"""

from __future__ import annotations

import pytest

from jobcrawler.runtime.safe_exec import (
    BANNED_BUILTINS,
    exec_crawler,
    module_allowed,
    safe_builtins,
)


def test_safe_builtins_omits_every_escape_hatch() -> None:
    builtins = safe_builtins()
    for name in BANNED_BUILTINS:
        if name == "__import__":
            continue  # replaced by the guard, not removed
        assert name not in builtins, f"{name} must not be reachable"


def test_safe_builtins_keeps_the_value_builtins_crawlers_need() -> None:
    # Removing too much would break legit crawlers — these must survive.
    builtins = safe_builtins()
    for name in (
        "len",
        "range",
        "enumerate",
        "isinstance",
        "sorted",
        "dict",
        "list",
        "str",
        "int",
        "Exception",
        "ValueError",
        "print",
        "__build_class__",  # `class`/@dataclass definitions need this
    ):
        assert name in builtins, f"{name} must stay available"


def test_safe_builtins_is_a_fresh_dict_each_call() -> None:
    # One crawler poisoning its builtins must not bleed into the next.
    a = safe_builtins()
    a["len"] = "tampered"
    assert safe_builtins()["len"] is len  # fresh dict → the real, untampered builtin


def test_module_allowed_admits_the_allowlist_and_rejects_the_rest() -> None:
    for ok in (
        "re",
        "json",
        "base64",  # ATS payloads (e.g. Gupy __NEXT_DATA__ tokens) are base64
        "urllib.parse",
        "collections.abc",
        "jobcrawler.runtime.contract",
        "lxml.etree",
        "selectolax.parser",
        "feedparser",
        "xml.etree.ElementTree",
    ):
        assert module_allowed(ok), ok
    for bad in ("os", "sys", "socket", "subprocess", "urllib.request", "shutil"):
        assert not module_allowed(bad), bad


def test_exec_crawler_admits_allowlisted_imports() -> None:
    ns = exec_crawler(
        "import re\n"
        "import json\n"
        "from collections.abc import Iterable\n"
        "from urllib.parse import urlparse\n"
        "pat = re.compile(r'a')\n",
        filename="<t>",
    )
    assert "pat" in ns


def test_exec_crawler_rejects_a_banned_import() -> None:
    with pytest.raises(ImportError):
        exec_crawler("import os\n", filename="<t>")


def test_exec_crawler_rejects_from_import_of_banned_module() -> None:
    with pytest.raises(ImportError):
        exec_crawler("from os import path\n", filename="<t>")


def test_exec_crawler_rejects_a_raw_dunder_import_call() -> None:
    # The escape an AST bypass would aim for: __import__("os") at runtime.
    with pytest.raises(ImportError):
        exec_crawler('__import__("os")\n', filename="<t>")


def test_exec_crawler_rejects_relative_import() -> None:
    with pytest.raises(ImportError):
        exec_crawler("from . import contract\n", filename="<t>")


def test_eval_is_a_nameerror_at_runtime() -> None:
    with pytest.raises(NameError):
        exec_crawler('eval("1 + 1")\n', filename="<t>")


def test_open_is_a_nameerror_at_runtime() -> None:
    with pytest.raises(NameError):
        exec_crawler('open("/etc/passwd")\n', filename="<t>")


def test_reaching_builtins_by_name_yields_no_escape_hatch() -> None:
    # If __builtins__ is somehow reached by name (the AST gate bans it, but defense
    # in depth), it is our restricted *dict* — indexing out eval is a KeyError.
    with pytest.raises(KeyError):
        exec_crawler('__builtins__["eval"]\n', filename="<t>")
