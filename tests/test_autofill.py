"""Tests for the §18 auto-apply prefill: queue store, guards, worker, drain loop.

No Chrome and no LLM anywhere: the worker takes an injected FakeBrowser that
dispatches ``evaluate()`` on the ``/*jc-*/`` marker comments the real JS carries,
and ``_call_llm`` is overwritten per-test (the Matcher fake-predictor pattern).
The no-submit guard is asserted on the shared Python/JS pattern source — the same
regex string the click executor compiles in the page.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, cast

import jobcrawler.autofill as autofill_mod
from jobcrawler.autofill import (
    _SUBMIT_RE,
    _WIZARD_RE,
    AutofillWorker,
    _click_js,
    _parse_actions,
)
from jobcrawler.cdp import CdpBrowser, CdpError
from jobcrawler.runtime.runner import CrawlSummary
from jobcrawler.schedule import Scheduler, Site
from jobcrawler.state import StateStore, autofill_target

FIELDS: dict[str, object] = {
    "title": "Staff Engineer",
    "company": "Acme",
    "location": "Remote",
}


# -- autofill_target (which positions get the button) -------------------------


def _target_for(apply_url: str | None, url: str = "https://board.test/a") -> str | None:
    store = StateStore(":memory:")
    fields = dict(FIELDS)
    if apply_url is not None:
        fields["apply_url"] = apply_url
    store.upsert_position("acme", url, fields=fields, content_hash="h1")
    rec = store.get_position("acme", url)
    assert rec is not None
    return autofill_target(rec)


def test_target_prefers_the_http_apply_url() -> None:
    assert _target_for("https://apply.acme.test/a") == "https://apply.acme.test/a"


def test_target_is_none_for_mailto_apply_even_with_an_http_listing() -> None:
    # an explicit "apply by email" beats falling back to the listing URL
    assert _target_for("mailto:jobs@acme.test") is None


def test_target_falls_back_to_the_listing_url() -> None:
    assert _target_for(None) == "https://board.test/a"


def test_target_is_none_when_nothing_is_http() -> None:
    assert _target_for(None, url="mailto:jobs@acme.test") is None


# -- store queue round-trip ----------------------------------------------------


def test_request_is_idempotent_while_queued_or_running() -> None:
    store = StateStore(":memory:")
    a = store.request_autofill("s", "u", "https://x.test/apply")
    assert store.request_autofill("s", "u", "https://x.test/apply") == a
    claimed = store.claim_autofill()
    assert claimed is not None and claimed["id"] == a
    assert claimed["status"] == "running" and claimed["started_at"]
    assert store.request_autofill("s", "u", "https://x.test/apply") == a
    store.finish_autofill(a, status="done", summary="ok")
    # once finished, a fresh press queues a fresh run
    assert store.request_autofill("s", "u", "https://x.test/apply") != a


def test_claim_takes_the_oldest_queued_and_then_empties() -> None:
    store = StateStore(":memory:")
    first = store.request_autofill("s1", "u1", "https://x.test/1")
    second = store.request_autofill("s2", "u2", "https://x.test/2")
    got = store.claim_autofill()
    assert got is not None and got["id"] == first
    got = store.claim_autofill()
    assert got is not None and got["id"] == second
    assert store.claim_autofill() is None


def test_finish_prunes_finished_rows_but_never_pending_ones() -> None:
    store = StateStore(":memory:")
    for i in range(5):
        rid = store.request_autofill(f"s{i}", f"u{i}", "https://x.test/f")
        assert store.claim_autofill() is not None
        store.finish_autofill(rid, status="done", retain=0)  # 0 = keep all
    running = store.request_autofill("keep_running", "ur", "https://x.test/r")
    assert store.claim_autofill() is not None  # claims `running` (oldest queued)
    last = store.request_autofill("s9", "u9", "https://x.test/9")
    assert store.claim_autofill() is not None  # claims `last`
    queued = store.request_autofill("keep_queued", "uq", "https://x.test/q")
    store.finish_autofill(last, status="failed", error="boom", retain=1)
    # the five old finished rows fell off the retain window...
    assert store.autofill_runs_for("s0", "u0") == []
    # ...but pending rows with older ids survived untouched
    run_row = store.autofill_runs_for("keep_running", "ur")
    assert run_row and run_row[0]["status"] == "running" and run_row[0]["id"] == running
    q_row = store.autofill_runs_for("keep_queued", "uq")
    assert q_row and q_row[0]["status"] == "queued" and q_row[0]["id"] == queued


def test_runs_for_excludes_the_transcript_and_orders_newest_first() -> None:
    store = StateStore(":memory:")
    rid = store.request_autofill("s", "u", "https://x.test/apply")
    assert store.claim_autofill() is not None
    store.finish_autofill(
        rid,
        status="done",
        summary="filled",
        rounds=2,
        fields_filled=7,
        transcript=[{"round": 1, "llm_raw": "x" * 10_000}],
    )
    rid2 = store.request_autofill("s", "u", "https://x.test/apply")
    runs = store.autofill_runs_for("s", "u")
    assert [r["id"] for r in runs] == [rid2, rid]
    assert runs[1]["fields_filled"] == 7
    assert all("transcript_json" not in r for r in runs)
    # the transcript itself is durably there for self-improvement
    raw = store._conn.execute(
        "SELECT transcript_json FROM autofill_runs WHERE id = ?", (rid,)
    ).fetchone()[0]
    assert json.loads(raw)[0]["round"] == 1


def test_reset_stale_fails_running_rows_and_unblocks_the_position() -> None:
    store = StateStore(":memory:")
    rid = store.request_autofill("s", "u", "https://x.test/apply")
    assert store.claim_autofill() is not None
    assert store.reset_stale_autofill() == 1
    runs = store.autofill_runs_for("s", "u")
    assert runs[0]["status"] == "failed"
    assert "restarted" in str(runs[0]["error"])
    assert store.reset_stale_autofill() == 0  # idempotent
    assert store.request_autofill("s", "u", "https://x.test/apply") != rid


# -- action parsing --------------------------------------------------------------


def test_parse_actions_reads_fenced_and_prose_wrapped_json() -> None:
    fenced = '```json\n[{"op": "fill", "ref": "c1", "value": "x"}]\n```'
    assert _parse_actions(fenced) == [{"op": "fill", "ref": "c1", "value": "x"}]
    prose = 'Here is my plan:\n[{"op": "done", "summary": "s"}]\nThat is all.'
    assert _parse_actions(prose) == [{"op": "done", "summary": "s"}]


def test_parse_actions_refuses_garbage_and_caps_the_batch() -> None:
    assert _parse_actions("I cannot help with that.") == []
    assert _parse_actions("[not json]") == []
    assert _parse_actions('{"op": "fill"}') == []  # an object, not an array
    assert _parse_actions('["str", 3, {"op": "done"}]') == [{"op": "done"}]
    oversized = json.dumps([{"op": "fill", "ref": f"c{i}"} for i in range(80)])
    assert len(_parse_actions(oversized)) == 40


# -- the no-submit guard (shared Python/JS pattern source) -----------------------


def _fold(text: str) -> str:
    """The exact fold the click executor applies before testing: NFD, strip
    combining accents (U+0300–U+036F), lowercase."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if not ("\u0300" <= c <= "\u036f")
    )


def test_submit_guard_blocks_final_submits_in_both_languages() -> None:
    for text in (
        "Submit application",
        "Submit",
        "Enviar candidatura",
        "Finalizar candidatura",
        "Send my application",
    ):
        assert re.search(_SUBMIT_RE, _fold(text)), text


def test_submit_guard_leaves_reveal_affordances_clickable() -> None:
    # "Apply"/"Candidatar-se" open or reveal the form on Lever/Ashby/Gupy boards —
    # blocking them would strand the worker on the landing page.
    for text in ("Apply now", "Apply", "Candidatar-se", "Ver vaga", "Quero me candidatar"):
        assert not re.search(_SUBMIT_RE, _fold(text)), text


def test_wizard_guard_blocks_step_advances_after_accent_fold() -> None:
    for text in ("Next", "Continue", "Continuar", "Próximo", "Avançar", "Save and continue"):
        assert re.search(_WIZARD_RE, _fold(text)), text
    assert not re.search(_WIZARD_RE, _fold("Add another position"))


def test_click_js_embeds_the_guard_patterns_and_the_ref() -> None:
    js = _click_js("b2")
    assert json.dumps("b2") in js
    assert json.dumps(_SUBMIT_RE) in js
    assert json.dumps(_WIZARD_RE) in js
    # target=_blank apply anchors must navigate the tab we drive, not a new one
    assert "el.target = '_self'" in js


# -- the worker, end to end over a fake browser ----------------------------------


class FakeBrowser:
    """Dispatches ``evaluate()`` on the ``/*jc-*/`` markers the harness JS carries.

    ``sweeps`` is an optional queue of per-round digests (popped front-first; the
    default ``sweep`` serves once it drains) so tests can model a page that
    changes after a click. ``targets`` is the ``Target.getTargets`` reply used by
    the popup-adoption path.
    """

    def __init__(self) -> None:
        self.connected = True
        self.tab_alive = True  # flip False to simulate the operator closing the tab
        self.opened: list[str] = []
        self.attached: list[str] = []
        self.cmds: list[str] = []
        self.acted: list[str] = []
        self.sids: set[str] = set()
        self.sweeps: list[dict[str, Any]] = []
        self.targets: list[dict[str, Any]] = []
        self.sweep: dict[str, Any] = {
            "url": "https://apply.acme.test/a",
            "title": "Apply — Acme",
            "controls": [
                {"ref": "c1", "tag": "input", "type": "text", "label": "Full name"}
            ],
            "buttons": [],
            "crossOriginFrames": 0,
        }

    async def connect(self) -> None:
        self.connected = True

    async def open_tab(self, url: str) -> str:
        self.opened.append(url)
        return "T1"

    async def attach(self, target_id: str) -> str:
        self.attached.append(target_id)
        sid = "S" + target_id.lstrip("T")
        self.sids.add(sid)
        return sid

    async def cmd(self, method: str, params: dict | None = None, *, session_id=None, timeout=None) -> dict:
        self.cmds.append(method)
        if method == "Target.getTargetInfo":
            if not self.tab_alive:
                raise CdpError("Target.getTargetInfo: No target with given id found")
            return {"targetInfo": {"targetId": (params or {}).get("targetId")}}
        if method == "Target.getTargets":
            return {"targetInfos": list(self.targets)}
        return {}

    async def evaluate(self, sid: str, expression: str, *, await_promise: bool = False, timeout=None):
        assert sid in self.sids
        if not self.tab_alive:
            raise CdpError("Runtime.evaluate: Cannot find context with specified id")
        if "/*jc-ready*/" in expression:
            return "complete"
        if "/*jc-count*/" in expression:
            return 3
        if "/*jc-sweep*/" in expression:
            return json.dumps(self.sweeps.pop(0) if self.sweeps else self.sweep)
        if "/*jc-act*/" in expression:
            self.acted.append(expression)
            return json.dumps({"ok": True})
        raise AssertionError(f"unexpected evaluate: {expression[:60]}")


def _worker_fixture(tmp_path: Path) -> tuple[StateStore, AutofillWorker, FakeBrowser, int]:
    store = StateStore(":memory:")
    store.upsert_position(
        "acme",
        "https://acme.test/a",
        fields={**FIELDS, "apply_url": "https://apply.acme.test/a"},
        content_hash="h1",
    )
    profile = tmp_path / "PROFILE.md"
    profile.write_text("# Profile\nName: Jane Doe\nRust + distributed systems.\n")
    run_id = store.request_autofill(
        "acme", "https://acme.test/a", "https://apply.acme.test/a"
    )
    fake = FakeBrowser()
    worker = AutofillWorker(
        store,
        model="openai/test",
        max_rounds=3,
        profile_path=str(profile),
        notes_path=str(tmp_path / "APPLY.md"),  # absent — quietly skipped
        resume_path="",  # pinned off: None would fall back to the CV.pdf default
        sites_path=str(tmp_path / "sites.yaml"),  # absent — no Found-via line
        browser=cast(CdpBrowser, fake),
    )
    return store, worker, fake, run_id


async def test_run_once_fills_and_logs_the_transcript(tmp_path: Path) -> None:
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    seen: list[tuple[str, str, str]] = []

    def fake_llm(profile: str, context: str, page: str) -> str:
        seen.append((profile, context, page))
        return json.dumps(
            [
                {"op": "fill", "ref": "c1", "value": "Jane Doe"},
                {"op": "done", "summary": "name filled; review the rest"},
            ],
            ensure_ascii=False,
        )

    worker._call_llm = fake_llm  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out == {
        "id": run_id,
        "status": "done",
        "site": "acme",
        "url": "https://acme.test/a",
    }
    # the tab opened on the position's apply target and CDP domains were enabled
    assert fake.opened == ["https://apply.acme.test/a"]
    assert "Page.enable" in fake.cmds
    # the LLM saw the profile, the position, and the page digest
    profile, context, page = seen[0]
    assert "Jane Doe" in profile
    assert "Staff Engineer at Acme" in context
    assert "Resume file: NOT configured" in context
    assert '"c1"' in page
    # one fill executed in the page
    assert len(fake.acted) == 1 and json.dumps("c1") in fake.acted[0]
    # the run row carries the outcome + counters, panel query excludes transcript
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    assert runs[0]["status"] == "done"
    assert runs[0]["summary"] == "name filled; review the rest"
    assert runs[0]["rounds"] == 1
    assert runs[0]["actions_applied"] == 1
    assert runs[0]["fields_filled"] == 1
    assert runs[0]["model"] == "openai/test"
    assert runs[0]["profile_hash"]
    # the self-improvement transcript holds the digest, raw output, and results
    raw = store._conn.execute(
        "SELECT transcript_json FROM autofill_runs WHERE id = ?", (run_id,)
    ).fetchone()[0]
    transcript = json.loads(raw)
    assert transcript[0]["page"]["controls"][0]["ref"] == "c1"
    assert "Jane Doe" in transcript[0]["llm_raw"]
    assert transcript[0]["results"][0]["result"]["ok"] is True
    # no registry in this fixture — the source-board line is dropped, not invented
    assert "Found via" not in seen[0][1]


def _capture_context(worker: AutofillWorker) -> list[str]:
    seen: list[str] = []

    def fake_llm(profile: str, context: str, page: str) -> str:
        seen.append(context)
        return json.dumps([{"op": "done", "summary": "nothing to fill"}])

    worker._call_llm = fake_llm  # ty: ignore[invalid-assignment]
    return seen


async def test_context_names_the_board_the_position_came_from(tmp_path: Path) -> None:
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    # aggregator entry (no company): the registry name answers "how did you hear"
    (tmp_path / "sites.yaml").write_text(
        '- slug: acme\n  name: "Acme Board"\n  url: https://acme.test/jobs\n'
    )
    seen = _capture_context(worker)
    await worker.run_once()
    assert "Found via: the Acme Board job board" in seen[0]
    assert "how you heard" in seen[0]


async def test_context_says_own_careers_site_for_a_company_board(tmp_path: Path) -> None:
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    (tmp_path / "sites.yaml").write_text(
        "- slug: acme\n"
        '  name: "Acme"\n'
        "  url: https://acme.test/jobs\n"
        '  company: "Acme"\n'
    )
    seen = _capture_context(worker)
    await worker.run_once()
    assert "Found via: the company's own careers site" in seen[0]


async def test_a_slug_missing_from_the_registry_drops_the_found_via_line(
    tmp_path: Path,
) -> None:
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    (tmp_path / "sites.yaml").write_text(
        '- slug: other_board\n  name: "Other"\n  url: https://other.test/jobs\n'
    )
    seen = _capture_context(worker)
    await worker.run_once()
    assert "Found via" not in seen[0]
    # queue is empty now
    assert await worker.run_once() is None


_BUTTON_ONLY_PAGE: dict[str, Any] = {
    "url": "https://apply.acme.test/a",
    "title": "Acme careers",
    "controls": [],
    "controls_total": 0,
    "buttons": [
        {
            "ref": "b1",
            "tag": "button",
            "type": "button",
            "text": "Apply for This Job",
            "in_form": False,
        }
    ],
    "cross_origin_frames": 0,
}


async def test_done_right_after_a_click_is_deferred_to_a_fresh_sweep(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The zero-fill bug: [click "Apply", done] in one batch must not end the run.

    The click reveals/navigates to the real form (BambooHR modal, WWR→ATS hop);
    honoring the same-round done means the form is never even swept. The done is
    deferred and the next round plans against the refreshed page.
    """
    monkeypatch.setattr(autofill_mod, "_POST_CLICK_SETTLE_S", 0.0)
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    fake.sweeps = [dict(_BUTTON_ONLY_PAGE)]  # round 2 falls back to the form sweep
    contexts: list[str] = []

    def fake_llm(profile: str, context: str, page: str) -> str:
        contexts.append(context)
        if len(contexts) == 1:
            return json.dumps(
                [
                    {"op": "click", "ref": "b1", "why": "open the form"},
                    {"op": "done", "summary": "opened the form; nothing visible yet"},
                ]
            )
        return json.dumps(
            [
                {"op": "fill", "ref": "c1", "value": "Jane Doe"},
                {"op": "done", "summary": "name filled"},
            ],
            ensure_ascii=False,
        )

    worker._call_llm = fake_llm  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out is not None and out["status"] == "done"
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    assert runs[0]["summary"] == "name filled"  # not the round-1 guess
    assert runs[0]["rounds"] == 2
    assert runs[0]["fields_filled"] == 1
    # round 2's context told the model its done was deferred
    assert "DEFERRED" in contexts[1]
    # the transcript records the deferral for the self-improvement log
    raw = store._conn.execute(
        "SELECT transcript_json FROM autofill_runs WHERE id = ?", (run_id,)
    ).fetchone()[0]
    round1 = json.loads(raw)[0]
    assert round1["results"][-1]["result"].get("deferred")


async def test_a_tab_the_page_opens_is_adopted_and_driven(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """window.open apply flows: the form lands in a new tab — follow it there."""
    monkeypatch.setattr(autofill_mod, "_POST_CLICK_SETTLE_S", 0.0)
    store, worker, fake, run_id = _worker_fixture(tmp_path)
    fake.sweeps = [dict(_BUTTON_ONLY_PAGE)]
    fake.targets = [
        {"type": "page", "targetId": "T1", "url": "https://apply.acme.test/a"},
        {
            "type": "page",
            "targetId": "T2",
            "openerId": "T1",
            "url": "https://ats.example.test/apply",
        },
        # someone else's tab: no opener — must never be grabbed
        {"type": "page", "targetId": "T9", "url": "https://mail.example.test/"},
    ]
    rounds = 0

    def fake_llm(profile: str, context: str, page: str) -> str:
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            return json.dumps([{"op": "click", "ref": "b1", "why": "open the form"}])
        return json.dumps(
            [
                {"op": "fill", "ref": "c1", "value": "Jane Doe"},
                {"op": "done", "summary": "filled on the ATS tab"},
            ],
            ensure_ascii=False,
        )

    worker._call_llm = fake_llm  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out is not None and out["status"] == "done"
    # the popup was attached and round 2 ran against it (S2 accepted the sweep)
    assert fake.attached == ["T1", "T2"]
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    assert runs[0]["summary"] == "filled on the ATS tab"
    assert runs[0]["fields_filled"] == 1
    raw = store._conn.execute(
        "SELECT transcript_json FROM autofill_runs WHERE id = ?", (run_id,)
    ).fetchone()[0]
    round1 = json.loads(raw)[0]
    assert round1["popup"] == {
        "target_id": "T2",
        "url": "https://ats.example.test/apply",
    }


async def test_closing_the_tab_interrupts_the_run(tmp_path: Path) -> None:
    """Operator closes the apply tab mid-run → prompt 'interrupted', not 'stalled'.

    A dead tab fails every action identically, which the stall branch would
    otherwise mislabel; the worker re-checks the target and raises TabClosed.
    """
    store, worker, fake, run_id = _worker_fixture(tmp_path)

    def fake_llm(profile: str, context: str, page: str) -> str:
        fake.tab_alive = False  # closed while the model was thinking
        return json.dumps(
            [{"op": "fill", "ref": "c1", "value": "Jane Doe"}], ensure_ascii=False
        )

    worker._call_llm = fake_llm  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out is not None and out["status"] == "failed"
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    error = str(runs[0]["error"])
    assert "interrupted" in error and "tab closed" in error
    assert "stalled" not in error
    # what ran before the close is preserved for the self-improvement log
    raw = store._conn.execute(
        "SELECT transcript_json FROM autofill_runs WHERE id = ?", (run_id,)
    ).fetchone()[0]
    transcript = json.loads(raw)
    assert transcript[0]["results"][0]["result"]["ok"] is False


async def test_a_transient_sweep_error_with_a_live_tab_stays_a_generic_failure(
    tmp_path: Path,
) -> None:
    """The interrupt path must not swallow real CDP breakage: tab alive → re-raise."""
    store, worker, fake, _run_id = _worker_fixture(tmp_path)
    orig = fake.evaluate

    async def flaky(sid: str, expression: str, **kw: Any) -> Any:
        if "/*jc-sweep*/" in expression:
            raise CdpError("Runtime.evaluate: transient")
        return await orig(sid, expression, **kw)

    fake.evaluate = flaky  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out is not None and out["status"] == "failed"
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    error = str(runs[0]["error"])
    assert error.startswith("CdpError:")
    assert "interrupted" not in error


async def test_run_once_ends_cleanly_when_the_model_returns_prose(tmp_path: Path) -> None:
    store, worker, _fake, _run_id = _worker_fixture(tmp_path)
    worker._call_llm = lambda profile, context, page: "I would fill the form."  # ty: ignore[invalid-assignment]
    out = await worker.run_once()
    assert out is not None and out["status"] == "done"
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    assert runs[0]["summary"] == "model returned no parseable actions"
    assert runs[0]["rounds"] == 1 and runs[0]["fields_filled"] == 0


async def test_run_once_lands_failures_on_the_row(tmp_path: Path) -> None:
    store = StateStore(":memory:")
    store.request_autofill("acme", "https://acme.test/a", "https://apply.acme.test/a")
    worker = AutofillWorker(
        store,
        model="openai/test",
        profile_path=str(tmp_path / "missing-profile.md"),
        notes_path="",
        resume_path="",
        browser=cast(CdpBrowser, FakeBrowser()),
    )
    out = await worker.run_once()
    assert out is not None and out["status"] == "failed"
    runs = store.autofill_runs_for("acme", "https://acme.test/a")
    assert runs[0]["status"] == "failed"
    assert "ProfileNotFound" in str(runs[0]["error"])
    # never raises, and an empty queue yields None
    assert await worker.run_once() is None


async def test_run_once_is_none_on_an_empty_queue(tmp_path: Path) -> None:
    store = StateStore(":memory:")
    worker = AutofillWorker(
        store,
        model="openai/test",
        profile_path=str(tmp_path / "PROFILE.md"),
        browser=cast(CdpBrowser, FakeBrowser()),
    )
    assert await worker.run_once() is None


# -- the scheduler's drain task ---------------------------------------------------


async def test_autofill_drain_loop_drains_the_backlog_back_to_back() -> None:
    backlog: list[dict[str, object]] = [
        {"id": 1, "status": "done", "site": "a", "url": "u1"},
        {"id": 2, "status": "failed", "site": "b", "url": "u2"},
    ]
    passes: list[object] = []

    async def drain() -> dict[str, object] | None:
        result = backlog.pop(0) if backlog else None
        passes.append(result)
        return result

    async def crawl(site: Site) -> CrawlSummary:
        raise AssertionError("no crawl in this test")

    sched = Scheduler(
        [],
        store=StateStore(":memory:"),
        crawl=crawl,
        autofill_drain=drain,
        autofill_poll=0.01,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sched._autofill_drain_loop(stop, poll=0.01))
    for _ in range(200):  # wait until the first pass fully drained (…, None)
        if len(passes) >= 3:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # both queued runs were drained in ONE pass (back-to-back), then the empty signal
    assert passes[:3] == [
        {"id": 1, "status": "done", "site": "a", "url": "u1"},
        {"id": 2, "status": "failed", "site": "b", "url": "u2"},
        None,
    ]


async def test_autofill_drain_loop_survives_a_raising_seam() -> None:
    calls = {"n": 0}

    async def drain() -> dict[str, object] | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("store hiccup")
        return None

    async def crawl(site: Site) -> CrawlSummary:
        raise AssertionError("no crawl in this test")

    sched = Scheduler(
        [],
        store=StateStore(":memory:"),
        crawl=crawl,
        autofill_drain=drain,
        autofill_poll=0.01,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sched._autofill_drain_loop(stop, poll=0.01))
    for _ in range(200):
        if calls["n"] >= 2:  # a second pass ran after the raise
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert calls["n"] >= 2
