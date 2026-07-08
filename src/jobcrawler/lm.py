"""Construct ``dspy.LM``s, routing ChatGPT-subscription (Codex OAuth) slugs (§9).

Most deployments name an ``openrouter/<vendor>/<model>`` slug and bill per token. A
``chatgpt/<model>`` slug instead routes through litellm's native ChatGPT provider — the
Codex *Responses* backend billed against a ChatGPT subscription (see :mod:`jobcrawler.auth`
for the OAuth token it reads). That backend needs two things a plain ``dspy.LM(...)`` call
gets wrong:

  1. ``model_type="responses"`` — it speaks the Responses API, not chat completions.
  2. **No** ``temperature`` / ``max_tokens`` — the Codex backend ignores sampling params
     (litellm strips everything but a fixed allow-list), and worse, dspy's reasoning-model
     guard *raises* if a ``gpt-5*`` slug is handed ``temperature != 1`` or
     ``max_tokens < 16000`` (``clients/lm.py`` in the pinned 3.2.1). The §4.7 matcher builds
     its LM at ``temperature=0, max_tokens=4000`` — exactly the shape that trips the guard —
     so those kwargs must be dropped for a ``chatgpt/*`` slug.
  3. **A repaired response transform** — litellm's stock Codex transform rebuilds the reply
     from the empty terminal ``response.completed`` event and discards the streamed output,
     so dspy crashes with ``KeyError: 'text'`` (0 jobs scored). :func:`_install_codex_responses_fix`
     patches that transform in place before the first request; see its comment for the details.

Every LM construction site (generation root/sub, GEPA reflection, the matcher) goes through
:func:`build_lm` so this routing lives in one place; a non-``chatgpt`` slug is constructed
exactly as before, preserving the OpenRouter behavior byte-for-byte.

``dspy`` is imported lazily inside :func:`build_lm` (never at module load) so
:func:`is_chatgpt` stays dependency-free — the run/crawl startup preflight imports it to
decide whether a token check is even needed, and must not pull dspy to do so.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import dspy

# The provider prefix litellm routes to its native ChatGPT (Codex OAuth) backend.
CHATGPT_PREFIX = "chatgpt/"


def is_chatgpt(model: str) -> bool:
    """True if ``model`` routes through litellm's ChatGPT (Codex OAuth) provider (§9)."""
    return model.startswith(CHATGPT_PREFIX)


# --- §9 Codex Responses backend: repair litellm's output-dropping transform -------
#
# litellm's ChatGPT/Codex provider (``chatgpt/*``) speaks the Responses API and forces
# server-side streaming, but its ``transform_response_api_response`` rebuilds the reply
# SOLELY from the terminal ``response.completed`` SSE event — whose ``output`` array
# comes back EMPTY for this backend. The message text is delivered incrementally, via
# ``response.output_text.delta`` and ``response.output_item.done`` events, and never
# re-embedded in the completed event. So the stock transform hands back a reply with no
# output items; dspy's ``base_lm._process_response`` then builds a dict with no ``text``
# key and its adapter indexes ``output["text"]`` unconditionally, raising a bare
# ``KeyError: 'text'`` that fails EVERY match batch (0 jobs scored, §4.7 fail-open parks
# them all). Both litellm and dspy are pinned, read-only store paths, so we repair the
# one broken method in place: read the SSE body once (as the original does) and refill
# the empty ``output`` from the streamed items/deltas. Installed once, idempotent, and a
# no-op if litellm's layout changes or it ever starts returning a populated ``output``.

_codex_responses_fix_installed = False


def _install_codex_responses_fix() -> None:
    """Idempotently patch litellm's Codex responses transform to keep the output (§9)."""
    global _codex_responses_fix_installed
    if _codex_responses_fix_installed:
        return
    # Set first: a failed/again-unavailable patch must not be retried on every build.
    _codex_responses_fix_installed = True
    try:
        from litellm.llms.chatgpt.responses.transformation import (
            ChatGPTResponsesAPIConfig,
        )
    except Exception:  # pragma: no cover - litellm layout changed; leave stock behavior
        return

    # Capture the stock method and delegate to our corrected walk. The module-level
    # flag above already guarantees this body runs at most once per process, so no
    # per-function re-patch marker is needed.
    stock = ChatGPTResponsesAPIConfig.transform_response_api_response

    def patched(self, model: str, raw_response: Any, logging_obj: Any) -> Any:
        return _codex_transform_keep_output(
            self, model, raw_response, logging_obj, stock
        )

    ChatGPTResponsesAPIConfig.transform_response_api_response = patched  # ty: ignore[invalid-assignment]


def _codex_transform_keep_output(
    self: Any, model: str, raw_response: Any, logging_obj: Any, stock: Any
) -> Any:
    """Corrected Codex responses transform: refill the dropped ``output`` array (§9).

    Mirrors litellm's own SSE walk (single read of ``raw_response.text``), but also
    collects the streamed ``output_item.done`` items and ``output_text.delta`` chunks
    and injects them into the completed event's empty ``output`` — guaranteeing a
    message item so downstream parsing always finds a ``text`` field (a reasoning- or
    tool-only reply degrades to an empty string instead of crashing).
    """
    import json

    from litellm.constants import STREAM_SSE_DONE_STRING
    from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import (
        _safe_convert_created_field,
    )
    from litellm.llms.openai.common_utils import OpenAIError
    from litellm.types.llms.openai import ResponsesAPIResponse, ResponsesAPIStreamEvents
    from litellm.utils import CustomStreamWrapper

    content_type = (raw_response.headers or {}).get("content-type", "")
    body_text = raw_response.text or ""
    trimmed = body_text.lstrip()
    looks_like_sse = (
        "text/event-stream" in content_type.lower()
        or trimmed.startswith("event:")
        or trimmed.startswith("data:")
        or "\nevent:" in body_text
        or "\ndata:" in body_text
    )
    if not looks_like_sse:
        # A plain JSON body — the stock transform already handles that correctly.
        return stock(self, model, raw_response, logging_obj)

    logging_obj.post_call(
        original_response=body_text, additional_args={"complete_input_dict": {}}
    )

    completed_payload = None
    error_message = None
    stream_items: list[dict[str, Any]] = []
    text_deltas: list[str] = []
    for chunk in body_text.splitlines():
        stripped = CustomStreamWrapper._strip_sse_data_from_chunk(chunk)
        if not stripped:
            continue
        stripped = stripped.strip()
        if not stripped:
            continue
        if stripped == STREAM_SSE_DONE_STRING:
            break
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                stream_items.append(item)
        elif event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_deltas.append(delta)
        elif event_type == ResponsesAPIStreamEvents.RESPONSE_COMPLETED:
            completed_payload = event.get("response")
            break
        elif event_type in (
            ResponsesAPIStreamEvents.RESPONSE_FAILED,
            ResponsesAPIStreamEvents.ERROR,
        ):
            err = event.get("error") or (event.get("response") or {}).get("error")
            if err is not None:
                error_message = (
                    err.get("message") if isinstance(err, dict) else str(err)
                )

    if not isinstance(completed_payload, dict):
        raise OpenAIError(
            message=error_message or body_text,
            status_code=raw_response.status_code,
        )

    completed_payload = dict(completed_payload)
    if "created_at" in completed_payload:
        completed_payload["created_at"] = _safe_convert_created_field(
            completed_payload["created_at"]
        )

    # The repair: refill the empty ``output`` from the streamed items, and guarantee a
    # message item so ``_process_response`` always yields a ``text`` field.
    output_items = list(completed_payload.get("output") or [])
    if not output_items:
        output_items = [it for it in stream_items if isinstance(it, dict)]
    has_message = any(
        isinstance(it, dict) and it.get("type") == "message" for it in output_items
    )
    if not has_message:
        output_items.append(
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "id": "msg_jobcrawler_aggregated",
                "content": [
                    {
                        "type": "output_text",
                        "text": "".join(text_deltas),
                        "annotations": [],
                    }
                ],
            }
        )
    completed_payload["output"] = output_items

    try:
        completed = ResponsesAPIResponse(**completed_payload)
    except Exception:
        completed = ResponsesAPIResponse.model_construct(**completed_payload)
    if not hasattr(completed, "_hidden_params"):
        completed._hidden_params = {}
    return completed


def build_lm(
    model: str,
    *,
    cls: type[dspy.LM] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> dspy.LM:
    """Build ``cls(model, ...)``, routing a ``chatgpt/*`` slug to the Codex backend (§9).

    For a ``chatgpt/*`` slug: force ``model_type="responses"`` and DROP
    ``temperature``/``max_tokens`` (the backend ignores them and dspy's gpt-5 guard rejects
    them), and pin the OAuth token env so litellm's provider finds the token ``jobcrawl
    login`` imported. For any other slug: pass ``temperature``/``max_tokens`` straight
    through, so the OpenRouter path is unchanged. ``cls`` (default ``dspy.LM``) lets a caller
    build a subclass — the §4.2 :class:`~jobcrawler.generate.budget.CappedLM` — with the same
    routing; extra ``kwargs`` (e.g. ``max_subcalls``) pass through to the constructor.
    """
    import dspy

    if cls is None:
        cls = dspy.LM

    if is_chatgpt(model):
        # Lazy import: only pull the auth module (and, through it, litellm) when a
        # chatgpt slug is actually in play. Pinning CHATGPT_TOKEN_DIR before the LM is
        # built is what makes litellm's provider read the store `jobcrawl login` wrote.
        from jobcrawler.auth import apply_auth_env

        apply_auth_env()
        # Repair litellm's Codex responses transform (drops the streamed output →
        # KeyError: 'text') before any request goes out. Idempotent; see above.
        _install_codex_responses_fix()
        kwargs.setdefault("model_type", "responses")
        return cls(model, **kwargs)

    # Non-chatgpt (the OpenRouter default): preserve the exact prior construction —
    # 0.0 is a meaningful temperature, so guard on `is not None`, not truthiness.
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return cls(model, **kwargs)


# --- litellm LoggingWorker hygiene: drain before a short-lived loop closes ---------
#
# litellm's global LoggingWorker lazily spawns a background asyncio task
# (``LoggingWorker._worker_loop``) inside whatever event loop is running when the first
# async completion finishes. Generation runs each attempt in its own ``asyncio.run``
# loop on a worker thread (§4.2/§6), and litellm never cancels that task when the loop
# goes away — on the next loop it merely drops its *reference* (``_ensure_queue`` on
# loop change). The stranded task then surfaces as ``Task was destroyed but it is
# pending!`` in the daemon's stderr (one per generation loop, batched at GC time), and
# whatever usage/success callbacks were still queued are silently dropped. The fix
# therefore belongs at OUR loop exits: flush what's queued (bounded), then stop the
# worker task cleanly so nothing outlives the loop.

_T = TypeVar("_T")


async def drain_litellm_logging(
    *, worker: Any | None = None, flush_timeout: float = 5.0
) -> None:
    """Flush and stop litellm's logging worker in the current event loop.

    Best-effort by design: a missing/re-laid-out litellm, a wedged callback, or a
    worker still bound to a foreign closed loop must never turn loop shutdown into a
    hang or an error — hence the bounded flush and the blanket excepts. ``worker``
    exists for tests; production callers always drain the global singleton.
    """
    if worker is None:
        try:
            from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
        except Exception:  # pragma: no cover - litellm layout changed; nothing to drain
            return
        worker = GLOBAL_LOGGING_WORKER
    try:
        # Run the queued callbacks rather than discarding them (usage accounting rides
        # on these), but never wait past the bound — the loop is shutting down.
        await asyncio.wait_for(worker.flush(), timeout=flush_timeout)
    except Exception:
        pass
    try:
        await worker.stop()
    except Exception:
        pass


def run_llm_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """``asyncio.run`` for an LLM-driving coroutine, draining litellm at loop exit.

    Every fresh-loop entry point that can reach litellm (the §4.2 generation paths,
    the optimizers' sync bridge) must come through here instead of a bare
    ``asyncio.run`` so the loop it creates never strands litellm's logging worker.
    The drain runs on failure too — a raising candidate must not leak the task.
    """

    async def _main() -> _T:
        try:
            return await coro
        finally:
            await drain_litellm_logging()

    return asyncio.run(_main())
