"""Capture the LLM interaction log of one crawler-generation attempt (§4.2).

Generation is **LLM-gated**: whether a candidate crawler is produced, and why a fan-out
came up empty, is decided by what the model was asked and what it returned. So the record
worth persisting for an attempt is the sequence of ``(prompt, completion)`` pairs its LMs
produced — not the host's ``print`` lines. This module attaches a small dspy callback to
the generation LMs that records each call into a **thread-local** buffer, scoped to one
attempt via :func:`begin_capture`/:func:`end_capture`.

Thread-local rather than a ``contextvar`` because each self-heal regeneration runs in its
own thread (``cli._build_healer`` → ``asyncio.to_thread`` → ``asyncio.run``), and dspy's
async LM path stays on that thread's event loop — so a per-thread buffer isolates
concurrent regenerations without one site's calls leaking into another's. The callback is
attached per-LM (``callbacks=[...]``), and ``dspy.LM.copy()`` deep-copies it onto every
per-candidate rollout copy, so the whole fan-out is covered; it only records while a buffer
is active, so for every other LM user (the §4.7 matcher) it is a cheap no-op. Capture never
raises into generation — dspy already wraps callback errors, and the accessors are defensive.
"""

from __future__ import annotations

import threading
from typing import Any

from dspy.utils.callback import BaseCallback

# Per prompt/completion character cap: keep a trace legible and bound the stored blob (a
# generation can make many calls with large corpus-bearing prompts). Truncation is marked.
_MAX_FIELD = 12_000

_state = threading.local()


def begin_capture() -> None:
    """Start capturing LM calls on THIS thread into a fresh buffer (one per attempt)."""
    _state.buffer = []
    _state.pending = {}


def end_capture() -> list[dict[str, Any]]:
    """Stop capturing on this thread; return the interactions recorded since :func:`begin_capture`."""
    buffer = getattr(_state, "buffer", None) or []
    _state.buffer = None
    _state.pending = {}
    return buffer


def _clip(text: object) -> str:
    string = "" if text is None else str(text)
    if len(string) <= _MAX_FIELD:
        return string
    return string[:_MAX_FIELD] + f"…[+{len(string) - _MAX_FIELD} chars truncated]"


def _render_prompt(messages: object, prompt: object) -> str:
    """A readable prompt string from dspy's ``messages`` (role-tagged) or the raw ``prompt``."""
    if isinstance(messages, list):
        return "\n\n".join(
            f"[{m.get('role', '?')}] {m.get('content', '')}"
            for m in messages
            if isinstance(m, dict)
        )
    return str(prompt or "")


def _render_outputs(outputs: object) -> str:
    """The completion text from an LM's return (a list of strings, or dicts with logprobs)."""
    if isinstance(outputs, list):
        parts: list[str] = []
        for out in outputs:
            if isinstance(out, dict):
                parts.append(str(out.get("text") or out.get("content") or out))
            else:
                parts.append(str(out))
        return "\n".join(parts)
    return str(outputs)


class _GenerationLMCapture(BaseCallback):
    """dspy callback recording each generation LM call (prompt/completion/model) to the buffer."""

    def on_lm_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        pending = getattr(_state, "pending", None)
        if pending is None:  # no active capture on this thread — cheap no-op
            return
        pending[call_id] = {
            "model": getattr(instance, "model", "?"),
            "prompt": _render_prompt(inputs.get("messages"), inputs.get("prompt")),
        }

    def on_lm_end(
        self, call_id: str, outputs: dict[str, Any] | None, exception: Exception | None = None
    ) -> None:
        buffer = getattr(_state, "buffer", None)
        if buffer is None:
            return
        started = getattr(_state, "pending", {}).pop(call_id, {})
        buffer.append(
            {
                "model": started.get("model", "?"),
                "prompt": _clip(started.get("prompt", "")),
                "completion": None
                if exception is not None
                else _clip(_render_outputs(outputs)),
                "error": None
                if exception is None
                else f"{type(exception).__name__}: {exception}"[:500],
            }
        )


_CALLBACK = _GenerationLMCapture()


def capture_callback() -> BaseCallback:
    """The singleton capture callback to attach to generation LMs via ``callbacks=[...]``."""
    return _CALLBACK
