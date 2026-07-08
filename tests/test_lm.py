"""Tests for LM construction routing (§9).

Every LM in the system is built through :func:`jobcrawler.lm.build_lm`, which sends a
``chatgpt/*`` slug to litellm's ChatGPT (Codex OAuth) Responses backend and any other slug
down the unchanged OpenRouter path. The two behaviors that must hold: a ``chatgpt/*`` slug
gets ``model_type="responses"`` and has ``temperature``/``max_tokens`` DROPPED (the backend
ignores them and dspy's gpt-5 guard rejects them), while a non-``chatgpt`` slug keeps both.
These are asserted by capturing the constructor kwargs — no LM is ever really built.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import dspy
import pytest

from jobcrawler import config, lm
from jobcrawler.generate.budget import CappedLM


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every ``dspy.LM.__init__`` call's ``(model, kwargs)`` without building an LM.

    Patching the base ``__init__`` also captures :class:`CappedLM` construction, which chains
    through ``super().__init__`` — so the ``cls=CappedLM`` routing is exercised too.
    """
    calls: list[dict[str, Any]] = []
    orig = dspy.LM.__init__

    def spy(self: dspy.LM, model: str, **kwargs: Any) -> None:
        calls.append({"model": model, "kwargs": kwargs})
        orig(self, model, **kwargs)

    monkeypatch.setattr(dspy.LM, "__init__", spy)
    return calls


def test_is_chatgpt_matches_only_the_provider_prefix() -> None:
    assert lm.is_chatgpt("chatgpt/gpt-5-codex") is True
    assert lm.is_chatgpt("openrouter/openai/gpt-4o") is False
    assert lm.is_chatgpt("gpt-5-codex") is False  # bare slug is not the litellm route


def test_non_chatgpt_slug_keeps_temperature_and_max_tokens(
    captured: list[dict[str, Any]],
) -> None:
    # The OpenRouter path is byte-for-byte the prior behavior: both sampling params pass
    # through and no responses model_type is forced.
    lm.build_lm("openrouter/test/model", temperature=0.0, max_tokens=4000)
    (call,) = captured
    assert call["model"] == "openrouter/test/model"
    assert call["kwargs"]["temperature"] == 0.0
    assert call["kwargs"]["max_tokens"] == 4000
    assert "model_type" not in call["kwargs"]


def test_chatgpt_slug_forces_responses_and_drops_sampling_params(
    captured: list[dict[str, Any]],
) -> None:
    # The exact shape the §4.7 matcher passes (temperature=0, max_tokens=4000) — which would
    # trip dspy's gpt-5 guard — must come out the other side stripped, model_type=responses.
    lm.build_lm("chatgpt/gpt-5-codex", temperature=0.0, max_tokens=4000)
    (call,) = captured
    assert call["model"] == "chatgpt/gpt-5-codex"
    assert call["kwargs"]["model_type"] == "responses"
    assert "temperature" not in call["kwargs"]
    assert "max_tokens" not in call["kwargs"]


def test_chatgpt_slug_pins_the_token_dir_env(
    captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Building a chatgpt LM must point litellm's provider at jobcrawler's token store so it
    # reads the credential `jobcrawl login` wrote — apply_auth_env sets CHATGPT_TOKEN_DIR.
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", str(tmp_path))
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    lm.build_lm("chatgpt/gpt-5-codex")
    assert config.auth_dir() == str(tmp_path)


def test_capped_lm_class_routes_through_the_same_logic(
    captured: list[dict[str, Any]],
) -> None:
    # The §4.2 sub_lm is a CappedLM; build_lm must construct that subclass (not a plain LM)
    # while applying the chatgpt routing and forwarding max_subcalls to its constructor.
    built = lm.build_lm(
        "chatgpt/gpt-5-codex", cls=CappedLM, max_subcalls=3, max_tokens=16000
    )
    assert isinstance(built, CappedLM)
    (call,) = captured
    assert call["kwargs"]["model_type"] == "responses"
    assert "max_tokens" not in call["kwargs"]  # dropped even for the capped path
    # max_subcalls is consumed by CappedLM.__init__, never forwarded to the base LM.
    assert "max_subcalls" not in call["kwargs"]


def test_capped_lm_non_chatgpt_keeps_max_tokens(
    captured: list[dict[str, Any]],
) -> None:
    built = lm.build_lm(
        "openrouter/test/model", cls=CappedLM, max_subcalls=3, max_tokens=16000
    )
    assert isinstance(built, CappedLM)
    (call,) = captured
    assert call["kwargs"]["max_tokens"] == 16000
    assert "model_type" not in call["kwargs"]


# --- §9 Codex responses transform repair -----------------------------------------
#
# litellm's stock Codex transform drops the streamed output and returns an empty reply,
# which crashes dspy with ``KeyError: 'text'`` and parks every match batch.
# :func:`lm._codex_transform_keep_output` walks the SSE body itself and refills the
# empty ``output``. These exercise that walk directly against synthetic SSE bodies —
# no network — so a regression in the aggregation is caught without live credentials.


class _FakeResponse:
    """Minimal stand-in for the httpx response litellm hands the transform."""

    def __init__(
        self, text: str, *, content_type: str = "text/event-stream", status: int = 200
    ) -> None:
        self.text = text
        self.headers = {"content-type": content_type}
        self.status_code = status


class _FakeLogging:
    def post_call(self, *args: Any, **kwargs: Any) -> None:
        pass


def _boom_stock(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("stock transform must not run for an SSE body")


def _sse(*events: dict[str, Any]) -> str:
    """Render event dicts as an SSE ``data:`` stream terminated by ``[DONE]``."""
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    lines.append("data: [DONE]")
    return "\n".join(lines)


def _delta(text: str) -> dict[str, Any]:
    return {"type": "response.output_text.delta", "delta": text}


def _item_done(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.output_item.done", "item": item}


def _message(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "id": "msg_test",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _completed(output: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # A stock Codex ``response.completed`` — note ``output`` comes back EMPTY, which is
    # exactly the defect the repair works around.
    return {
        "type": "response.completed",
        "response": {
            "id": "resp_test",
            "object": "response",
            "created_at": 1234567890,
            "status": "completed",
            "model": "gpt-5.5",
            "output": output if output is not None else [],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
        },
    }


def _transform(body: str, *, content_type: str = "text/event-stream") -> Any:
    return lm._codex_transform_keep_output(
        None,
        "gpt-5.5",
        _FakeResponse(body, content_type=content_type),
        _FakeLogging(),
        _boom_stock,
    )


def _output_items(response: Any) -> list[Any]:
    return list(getattr(response, "output", None) or [])


def _item_type(item: Any) -> Any:
    return item.get("type") if isinstance(item, dict) else getattr(item, "type", None)


def _message_text(response: Any) -> str:
    """Concatenate all ``output_text`` from message items — object OR dict shaped.

    ``ResponsesAPIResponse`` coerces valid payloads into typed items but falls back to
    ``model_construct`` (raw dicts) on a partial one, so tolerate both here.
    """
    out: list[str] = []
    for item in _output_items(response):
        if _item_type(item) != "message":
            continue
        content = (
            item.get("content")
            if isinstance(item, dict)
            else getattr(item, "content", None)
        )
        for part in content or []:
            text = (
                part.get("text")
                if isinstance(part, dict)
                else getattr(part, "text", None)
            )
            if isinstance(text, str):
                out.append(text)
    return "".join(out)


def test_codex_transform_refills_output_from_the_streamed_message() -> None:
    # The normal case: a complete message rides in on output_item.done while the
    # completed event's output is empty — the message (and its text) must survive.
    body = _sse(
        _delta("Hel"),
        _delta("lo"),
        _item_done(_message("Hello")),
        _completed(),
    )
    response = _transform(body)
    messages = [i for i in _output_items(response) if _item_type(i) == "message"]
    assert messages, "the repaired output must contain a message item"
    assert _message_text(response) == "Hello"


def test_codex_transform_synthesizes_a_message_from_deltas_only() -> None:
    # No output_item.done at all — the text lives solely in the deltas, so a message
    # item must be synthesized from them (otherwise dspy sees no `text`).
    body = _sse(_delta("O"), _delta("K"), _completed())
    response = _transform(body)
    assert _message_text(response) == "OK"


def test_codex_transform_degrades_reasoning_only_reply_to_empty_text() -> None:
    # A reasoning/tool-only reply carries no message and no deltas. Rather than crash
    # with KeyError: 'text', the repair guarantees an (empty) message so the batch
    # parks gracefully. The reasoning item itself is preserved.
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
    body = _sse(_item_done(reasoning), _completed())
    response = _transform(body)
    assert any(_item_type(i) == "reasoning" for i in _output_items(response))
    assert any(_item_type(i) == "message" for i in _output_items(response))
    assert _message_text(response) == ""


def test_codex_transform_raises_without_a_completed_event() -> None:
    from litellm.llms.openai.common_utils import OpenAIError

    # A truncated stream (deltas, never completed) is a genuine failure — surface it so
    # the caller parks the batch, don't fabricate a success.
    body = "data: " + json.dumps(_delta("partial"))
    with pytest.raises(OpenAIError):
        _transform(body)


def test_codex_transform_delegates_a_plain_json_body_to_stock() -> None:
    # A non-streaming JSON body is already handled correctly upstream — the repair must
    # defer to the stock transform rather than mis-parse it as SSE.
    sentinel = object()

    def stock(_self: Any, _model: str, _raw: Any, _log: Any) -> Any:
        return sentinel

    body = json.dumps({"id": "resp", "object": "response", "output": []})
    result = lm._codex_transform_keep_output(
        None,
        "gpt-5.5",
        _FakeResponse(body, content_type="application/json"),
        _FakeLogging(),
        stock,
    )
    assert result is sentinel


def test_install_codex_responses_fix_replaces_the_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litellm.llms.chatgpt.responses.transformation import ChatGPTResponsesAPIConfig

    stock = ChatGPTResponsesAPIConfig.transform_response_api_response
    # Pre-register with monkeypatch so the stock method is restored on teardown no matter
    # what install() assigns, and reset the module's one-shot guard so install() runs.
    monkeypatch.setattr(
        ChatGPTResponsesAPIConfig, "transform_response_api_response", stock
    )
    monkeypatch.setattr(lm, "_codex_responses_fix_installed", False)

    lm._install_codex_responses_fix()

    installed = ChatGPTResponsesAPIConfig.transform_response_api_response
    assert installed is not stock
    assert installed.__name__ == "patched"


# --- litellm LoggingWorker drain ---------------------------------------------------
#
# litellm lazily spawns its LoggingWorker task inside whatever loop is running when the
# first async completion lands. Generation drives each attempt in its own short-lived
# ``asyncio.run`` loop, which used to strand that task at loop close ("Task was
# destroyed but it is pending!" in the daemon log, one per loop) and silently drop the
# queued callbacks. ``run_llm_loop``/``drain_litellm_logging`` must run what's queued
# and stop the worker before the loop closes — success or failure alike. These tests
# own their loops via ``asyncio.run``, so they are deliberately sync.


def test_drain_flushes_queued_callbacks_and_stops_the_worker() -> None:
    from litellm.litellm_core_utils.logging_worker import LoggingWorker

    worker = LoggingWorker()
    ran: list[str] = []

    async def callback() -> None:
        ran.append("x")

    async def main() -> None:
        worker.ensure_initialized_and_enqueue(callback())
        await lm.drain_litellm_logging(worker=worker)
        # The invariant that kills the "Task was destroyed" noise: nothing of the
        # worker survives to be stranded when this loop closes.
        assert worker._worker_task is None

    asyncio.run(main())
    assert ran == ["x"]  # the queued callback was executed, not dropped


def test_run_llm_loop_returns_the_value_and_leaves_no_global_worker_task() -> None:
    # Holds under both regimes: with the stock worker the drain flushes and stops it;
    # with the production neuter installed (any earlier build_lm) nothing ever starts.
    # Either way the invariant is the same — no task survives to be stranded.
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    async def callback() -> None:
        pass

    async def work() -> str:
        GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue(callback())
        return "done"

    assert lm.run_llm_loop(work()) == "done"
    assert GLOBAL_LOGGING_WORKER._worker_task is None


def test_run_llm_loop_drains_even_when_the_coroutine_raises() -> None:
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    async def callback() -> None:
        pass

    async def work() -> None:
        GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue(callback())
        raise RuntimeError("candidate failed")

    # A raising generation candidate must still leave the loop clean behind it.
    with pytest.raises(RuntimeError, match="candidate failed"):
        lm.run_llm_loop(work())
    assert GLOBAL_LOGGING_WORKER._worker_task is None


def test_run_llm_loop_is_a_plain_runner_when_litellm_never_ran() -> None:
    # The common no-LLM path (e.g. a generation that fails before any call): the
    # drain must be a silent no-op, not an error.
    async def work() -> int:
        return 7

    assert lm.run_llm_loop(work()) == 7


def test_neutered_global_worker_never_spawns_a_task_in_an_ephemeral_loop() -> None:
    # dspy bridges sync→async with bare asyncio.run (Tool.__call__, syncify) — loops
    # jobcrawler can't wrap. After the neuter (installed by every build_lm), litellm
    # enqueueing inside such a loop must create NO worker task (nothing to strand at
    # loop close) and must close the telemetry coroutine instead of leaking it.
    import inspect

    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    lm._neuter_litellm_logging_worker()

    async def telemetry() -> None:
        raise AssertionError("a neutered worker must never execute telemetry")

    coro = telemetry()

    async def ephemeral() -> None:
        GLOBAL_LOGGING_WORKER.ensure_initialized_and_enqueue(coro)
        assert GLOBAL_LOGGING_WORKER._worker_task is None

    asyncio.run(ephemeral())
    assert inspect.getcoroutinestate(coro) == "CORO_CLOSED"
