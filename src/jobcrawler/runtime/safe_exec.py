"""Restricted exec environment for untrusted crawler code (§8 trusted computing base).

The §6 AST gate (:mod:`jobcrawler.generate.gates`) statically rejects
dangerous imports and builtins, but the host must never rely on the gate alone: a
committed or self-healed crawler is still model-written, untrusted code. So both
subprocess entries (:mod:`._sandbox_entry`, :mod:`._collect_entry`) exec it with a
**restricted** ``__builtins__`` — the real builtins minus the escape hatches
(``eval``/``exec``/``open``/``compile``/``getattr``/…), with ``__import__`` replaced
by a guard that admits only the same module allowlist the AST gate enforces.

This module is the single source of truth for that allow/deny list; ``gates.py``
imports it, so the static gate and the runtime sandbox can never drift apart and
leave a hole one side has closed. Even if a crawler slips an escape past the AST
gate, ``eval``/``exec``/``open``/raw ``__import__`` are simply absent at runtime.

Stdlib-only on purpose, so the sandbox child still starts fast under the memory
rlimit (no dspy, no heavy transitive imports — in particular it must never import
``generate.gates``/``generate.scoring``, which pull dspy).
"""

from __future__ import annotations

import builtins as _builtins
from collections.abc import Callable, Mapping, Sequence
from typing import Any

# -- module allowlist (shared with the §6 AST gate) --------------------------
# Exact module names a generated crawler may import. urllib is intentionally
# narrowed to ``urllib.parse`` (``urllib.request`` would be a network egress path
# outside ctx). __future__ is the harmless annotations directive. ``base64`` is
# admitted because some ATS boards ship their payload base64-encoded — Gupy embeds
# each posting's token in ``__NEXT_DATA__`` as base64 — and it is pure stdlib
# string↔bytes transcoding with no filesystem or network I/O, so it opens no
# escape the SSRF/ctx boundary doesn't already cover.
ALLOWED_EXACT = frozenset(
    {
        "__future__",
        "re",
        "json",
        "base64",
        "datetime",
        "urllib.parse",
        "collections",
        "collections.abc",
        "itertools",
        "math",
        "html",
        "typing",
        "dataclasses",
        "jobcrawler.runtime.contract",
    }
)
# ``string`` is deliberately absent: its only sandbox-relevant surface is
# ``string.Formatter``, whose ``format``/``vformat``/``get_field`` methods parse a
# replacement-field mini-language and resolve attribute/index access at runtime —
# the same ``{0.__class__...}`` dunder-pivot the §6 AST gate bans on
# ``str.format``. No crawler needs the module (f-strings + ``str`` methods cover
# it), so withholding it closes the Formatter route the AST attribute ban cannot
# see. Keep it off the allowlist.
# Parser packages: the package and any submodule (lxml.etree, selectolax.parser).
# stdlib ``xml`` is permitted because the §8 hardening (runtime/hardening.py,
# applied before any crawler runs) defuses XXE / entity-expansion in every path.
ALLOWED_PREFIXES = ("selectolax", "lxml", "feedparser", "xml")

# Builtins that grant filesystem / eval / introspection escape. Removed from the
# crawler's ``__builtins__`` at runtime AND rejected by name in the AST gate.
# ``__import__`` is removed here and replaced by :func:`_guarded_import`.
BANNED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "open",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    }
)


def module_allowed(name: str) -> bool:
    """True if ``name`` is an importable module on the §6 allowlist."""
    if name in ALLOWED_EXACT:
        return True
    return any(name == p or name.startswith(p + ".") for p in ALLOWED_PREFIXES)


# The genuine importer, captured before we ever hand crawlers a restricted one.
# Typed loosely because typeshed's ``__import__`` stub forbids the ``fromlist=None``
# the CPython import statement actually passes for a plain ``import x``.
_REAL_IMPORT: Callable[..., Any] = _builtins.__import__


def _guarded_import(
    name: str,
    globals_: Mapping[str, object] | None = None,
    locals_: Mapping[str, object] | None = None,
    fromlist: Sequence[str] | None = (),
    level: int = 0,
) -> object:
    """An ``__import__`` that admits only allowlisted, absolute imports.

    A crawler's ``import`` statements route through this (CPython looks up
    ``__builtins__['__import__']``), so it enforces the *same* module allowlist the
    AST gate does — a runtime ``__import__`` can't widen what static analysis closed.
    Transitive imports made *inside* an allowlisted library use that library's own
    real ``__import__``, not this one, so they are unaffected.
    """
    if level != 0:
        raise ImportError("relative import is not permitted in crawler code")
    if not module_allowed(name):
        raise ImportError(f"import of {name!r} is not permitted in crawler code")
    return _REAL_IMPORT(name, globals_, locals_, fromlist, level)


def safe_builtins() -> dict[str, object]:
    """The real builtins minus the escape hatches, with a guarded ``__import__``.

    A fresh dict per call so one crawler cannot mutate the builtins the next one sees.
    """
    safe: dict[str, object] = {
        name: value
        for name, value in vars(_builtins).items()
        if name not in BANNED_BUILTINS
    }
    safe["__import__"] = _guarded_import
    return safe


def exec_crawler(source: str, *, filename: str) -> dict[str, object]:
    """Compile and exec already-AST-gated crawler ``source`` under restricted builtins.

    Returns the populated module namespace; the caller pulls ``crawl`` out of it.
    Seeding ``__builtins__`` with :func:`safe_builtins` is the runtime half of the §8
    sandbox: even past the AST gate, ``eval``/``exec``/``open``/raw ``__import__`` are
    absent and ``import`` is allowlisted.
    """
    namespace: dict[str, object] = {"__builtins__": safe_builtins()}
    exec(compile(source, filename, "exec"), namespace)  # noqa: S102
    return namespace
