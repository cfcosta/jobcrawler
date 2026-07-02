"""A deterministic, Deno-free CodeInterpreter for RLM tests (§9).

dspy 3.2.1 ships no importable MockInterpreter (its reference lives in the dspy
repo's ``tests/``, not the wheel), so we author one — adapted from jobcrawler. It
satisfies the structural :class:`dspy.primitives.CodeInterpreter` protocol that
``dspy.RLM`` drives: a mutable ``tools`` dict RLM updates with ``llm_query`` before
each loop, an ``output_fields`` attribute it sets from the signature, and a
``_tools_registered`` flag it resets. ``execute`` ignores the action code and
returns the next queued result (or calls ``execute_fn``), recording every call in
``call_history`` so tests can assert the real RLM loop drove this sandbox rather
than bypassing it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class MockInterpreter:
    def __init__(
        self,
        results: list[Any] | None = None,
        *,
        execute_fn: Callable[[MockInterpreter, str, dict[str, Any] | None], Any]
        | None = None,
    ) -> None:
        self._results = list(results or [])
        self._execute_fn = execute_fn
        self.tools: dict[str, Callable[..., str]] = {}
        self.output_fields: list[dict[str, Any]] | None = None
        self._tools_registered = False
        self.call_history: list[tuple[str, dict[str, Any] | None]] = []

    def start(self) -> None:  # idempotent no-op
        pass

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        self.call_history.append((code, variables))
        if self._execute_fn is not None:
            return self._execute_fn(self, code, variables)
        return self._results.pop(0)

    def shutdown(self) -> None:  # RLM never calls this for an injected interpreter
        pass
