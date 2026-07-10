"""§18 auto-apply prefill: drive the operator's Chrome over CDP and let an LLM
fill a job-application form from ``PROFILE.md`` — never submitting.

Boundary contract (mirrors §4.7 scoring):

* The web UI only ENQUEUES (``autofill_runs`` status ``queued``); this worker,
  owned by the daemon's own drain task, does the CDP + LLM work.
* Every round's page digest, raw LLM output, and executed action results are
  persisted to ``autofill_runs.transcript_json`` so later self-improvement can
  mine real page→actions traces — the ``generation_log.llm_log`` pattern.
* NO SUBMIT is mechanical, not just prompted: the action space has no submit op
  and no raw JS (the LLM only names ``ref`` ids from our own page sweep); the
  ``click`` executor refuses submit-typed controls inside a form and
  submit-worded controls anywhere, and refuses wizard next/continue advances
  inside a form; no Enter keystroke is ever dispatched — trusted CDP input is
  limited to left-clicks at coordinates our own sweep located, combobox filter
  text, and Escape (plain fills use the native value setter + input/change
  events, not the keyboard); file uploads can only attach the one
  operator-configured resume path. The tab is deliberately left OPEN so the
  operator reviews and submits themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import dspy
import yaml
from pydantic import ValidationError

from jobcrawler import config
from jobcrawler.cdp import CdpBrowser, CdpError
from jobcrawler.lm import build_lm
from jobcrawler.profile import load_profile
from jobcrawler.sites import find_site, load_sites
from jobcrawler.state import PositionRecord, StateStore, autofill_target

_log = logging.getLogger("jobcrawler.autofill")

_MAX_ACTIONS_PER_ROUND = 40
_PAGE_JSON_BUDGET = 24_000  # chars of page digest per prompt
_PROFILE_BUDGET = 16_000
# SPA apply flows (BambooHR's modal, client-side route hops) keep rendering well
# past readyState=complete — give a click's effect a beat before re-reading.
_POST_CLICK_SETTLE_S = 2.0


class TabClosed(RuntimeError):
    """The operator closed the apply tab mid-run — their interrupt signal.

    Raised wherever a CDP failure turns out to be a vanished tab, so the run
    ends promptly with a clear "interrupted" outcome instead of burning rounds
    on a dead session or mislabeling the close as "stalled".
    """

# Guard wordlists (folded: lowercase, accents stripped — the JS folds the button
# text the same way before testing). SUBMIT words block a click anywhere; WIZARD
# words block only inside a <form>, where next/continue is a step-submit. Reveal
# affordances ("Apply", "Candidatar-se") are deliberately NOT here — clicking
# those is how the worker reaches the form on Lever/Ashby/Gupy-style boards.
_SUBMIT_WORDS = (
    "submit",
    "enviar",
    "finalizar candidatura",
    "send application",
    "send my application",
    "complete application",
)
_WIZARD_WORDS = (
    "next",
    "continue",
    "continuar",
    "proximo",
    "avancar",
    "save and continue",
    "review application",
)


def _words_pattern(words: tuple[str, ...]) -> str:
    """A regex source usable by BOTH Python ``re`` and JS ``new RegExp``."""
    return r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b"


_SUBMIT_RE = _words_pattern(_SUBMIT_WORDS)
_WIZARD_RE = _words_pattern(_WIZARD_WORDS)


# --- page JS (fixed harness code; the LLM never authors JS) -------------------
# Marker comments (/*jc-...*/) identify each snippet so tests can fake
# CdpBrowser.evaluate by dispatch instead of matching whole programs.

_READY_JS = (
    "/*jc-ready*/ (location.href === 'about:blank' ? 'blank' : document.readyState)"
)

_CONTROL_COUNT_JS = (
    "/*jc-count*/ document.querySelectorAll("
    "'input:not([type=hidden]), textarea, select').length"
)

# One sweep: collect every fillable control + clickable affordance (main frame and
# same-origin iframes), stash live element refs in window.__jcAF keyed by ref id,
# and return a compact JSON digest the LLM plans against. Refs are only valid for
# the round that swept them — every round re-sweeps (navigation resets the map).
_SWEEP_JS = r"""/*jc-sweep*/ (() => {
  const clip = (s, n) => {
    s = (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
    return s.length > n ? s.slice(0, n) + '…' : s;
  };
  const map = {};
  let n = 0;
  const controls = [];
  const buttons = [];
  let crossOrigin = 0;
  const visible = (el) => {
    try {
      const r = el.getBoundingClientRect();
      const st = el.ownerDocument.defaultView.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
    } catch (e) { return true; }
  };
  const isCombo = (el) => el.tagName === 'INPUT' && (
    (el.getAttribute('role') || '').toLowerCase() === 'combobox'
    || el.getAttribute('aria-autocomplete') === 'list'
    || el.getAttribute('aria-haspopup') === 'listbox');
  const comboValue = (el) => {
    // React-select keeps its <input> empty even after a choice commits; the
    // selection renders in a sibling "value container" (single-value/chips)
    // alongside a placeholder div. Read that, minus placeholder and input.
    try {
      const vc = el.closest('[class*="value-container"], [class*="ValueContainer"]');
      if (!vc) return '';
      const c = vc.cloneNode(true);
      for (const kill of c.querySelectorAll('input, [class*="placeholder"], [id$="placeholder"]')) kill.remove();
      return clip(c.textContent, 100);
    } catch (e) { return ''; }
  };
  const labelFor = (el, doc) => {
    let t = '';
    try {
      if (el.labels && el.labels.length) t = el.labels[0].textContent;
      if (!t) t = el.getAttribute('aria-label') || '';
      if (!t && el.getAttribute('aria-labelledby')) {
        const ref = doc.getElementById(el.getAttribute('aria-labelledby').split(/\s+/)[0]);
        if (ref) t = ref.textContent;
      }
      if (!t) { const wrap = el.closest('label'); if (wrap) t = wrap.textContent; }
      if (!t && el.id) {
        const lab = doc.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lab) t = lab.textContent;
      }
      if (!t) {
        const p = el.closest('div,fieldset,li,td,section');
        if (p) { const lab = p.querySelector('label, legend'); if (lab) t = lab.textContent; }
      }
    } catch (e) {}
    return clip(t, 140);
  };
  const walk = (doc) => {
    for (const el of doc.querySelectorAll('input, textarea, select')) {
      const type = String(el.type || '').toLowerCase();
      if (['hidden', 'submit', 'image', 'button', 'reset'].includes(type)) continue;
      const ref = 'c' + (++n);
      map[ref] = el;
      const item = {
        ref,
        tag: el.tagName.toLowerCase(),
        type: type || null,
        name: clip(el.name, 80) || null,
        id: clip(el.id, 80) || null,
        label: labelFor(el, doc) || null,
        placeholder: clip(el.placeholder, 100) || null,
        required: !!el.required,
        visible: visible(el),
      };
      if (el.tagName === 'SELECT') {
        item.value = clip(el.value, 100);
        item.options = [...el.options].slice(0, 60).map((o) => ({
          value: clip(o.value, 80), text: clip(o.textContent, 80), selected: o.selected,
        }));
      } else if (type === 'checkbox' || type === 'radio') {
        item.checked = !!el.checked;
        item.value = clip(el.value, 80);
      } else if (type === 'file') {
        item.value = el.files && el.files.length ? clip(el.files[0].name, 80) : '';
      } else {
        item.value = clip(el.value, 160);
        if (isCombo(el)) {
          item.combobox = true;
          if (!item.value) item.value = comboValue(el);
        }
      }
      controls.push(item);
    }
    for (const el of doc.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"], a[href]')) {
      if (buttons.length >= 60) break;
      const text = clip(el.textContent || el.value || el.getAttribute('aria-label'), 90);
      if (!text || !visible(el)) continue;
      if (el.tagName === 'A') {
        const href = el.getAttribute('href') || '';
        if (!/apply|candidat|vaga|job|application|start|inscri/i.test(text + ' ' + href)) continue;
        const ref = 'b' + (++n);
        map[ref] = el;
        buttons.push({ ref, tag: 'a', text, href: clip(href, 120) });
        continue;
      }
      const ref = 'b' + (++n);
      map[ref] = el;
      buttons.push({
        ref,
        tag: el.tagName.toLowerCase(),
        type: String(el.type || '').toLowerCase() || null,
        text,
        in_form: !!el.closest('form'),
        disabled: !!el.disabled,
      });
    }
    for (const f of doc.querySelectorAll('iframe')) {
      try { if (f.contentDocument) walk(f.contentDocument); else crossOrigin++; }
      catch (e) { crossOrigin++; }
    }
  };
  walk(document);
  window.__jcAF = map;
  const heads = [...document.querySelectorAll('h1,h2,h3')].slice(0, 10)
    .map((h) => clip(h.textContent, 100)).filter(Boolean);
  return JSON.stringify({
    url: location.href,
    title: clip(document.title, 120),
    headings: heads,
    cross_origin_frames: crossOrigin,
    controls: controls.slice(0, 150),
    controls_total: controls.length,
    buttons,
  });
})()"""

_FILL_JS = r"""/*jc-act*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
  const value = __VALUE__;
  try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
  const tag = el.tagName;
  if (tag === 'INPUT' && ((el.getAttribute('role') || '').toLowerCase() === 'combobox'
      || el.getAttribute('aria-autocomplete') === 'list'
      || el.getAttribute('aria-haspopup') === 'listbox'))
    // Typed text in a React/ARIA combobox only filters the menu — it never
    // commits a choice (run 7 "filled" these ok and the form stayed empty).
    return JSON.stringify({ ok: false, error: 'combobox — use select with the visible option text' });
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    const proto = tag === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    try { el.focus(); } catch (e) {}
    if (desc && desc.set) desc.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    try { el.blur(); } catch (e) {}
    return JSON.stringify({ ok: true, value: String(el.value).slice(0, 120) });
  }
  if (el.isContentEditable) {
    try { el.focus(); } catch (e) {}
    el.textContent = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return JSON.stringify({ ok: true, value: value.slice(0, 120) });
  }
  return JSON.stringify({ ok: false, error: 'not a fillable element: ' + tag });
})()"""

_SELECT_JS = r"""/*jc-act*/ /*jc-select*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
  const want = String(__VALUE__);
  const fold = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const clip = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().slice(0, 60);
  try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
  if (el.tagName !== 'SELECT') {
    if (el.tagName === 'INPUT' && ((el.getAttribute('role') || '').toLowerCase() === 'combobox'
        || el.getAttribute('aria-autocomplete') === 'list' || el.getAttribute('aria-haspopup') === 'listbox'))
      // Sentinel: the worker drives comboboxes as a multi-step open/poll/pick
      // sequence (page timers throttle in background tabs, so the waiting
      // between steps happens worker-side, never in page JS).
      return JSON.stringify({ ok: false, combo: true, error: 'combobox' });
    return JSON.stringify({ ok: false, error: 'not a <select> or combobox' });
  }
  let hit = null;
  for (const o of el.options) if (o.value === want) { hit = o; break; }
  if (!hit) {
    const lw = fold(want);
    for (const o of el.options) if (fold(o.textContent) === lw) { hit = o; break; }
    if (!hit) for (const o of el.options) if (fold(o.textContent).includes(lw)) { hit = o; break; }
  }
  if (!hit) return JSON.stringify({
    ok: false, error: 'no option matches ' + want.slice(0, 60),
    options: [...el.options].slice(0, 12).map((o) => clip(o.textContent)),
  });
  el.value = hit.value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return JSON.stringify({ ok: true, value: hit.textContent.trim().slice(0, 120) });
})()"""

# React/ARIA combobox steps (Greenhouse job-boards renders every dropdown as a
# react-select <input role=combobox>): options mount only while the menu is
# open, and a selection commits by CLICKING an option — assigning the input's
# value never sticks (run 7 "filled" these ok:true and the form stayed empty).
# These snippets only READ the page (find the widget, count/match options,
# report click coordinates); the worker performs the actual click/typing with
# TRUSTED CDP Input events, because bot-hardened React builds (Greenhouse's
# Remix app) ignore dispatchEvent-synthesized events outright — isTrusted is
# the one thing page JS cannot fake. Waits also live worker-side: page timers
# in a backgrounded tab are throttled to ~one wake per minute.

_COMBO_HELPERS_JS = r"""
  const fold = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const clip = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim().slice(0, 60);
  const doc = el.ownerDocument;
  const shown = (o) => { const r = o.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const center = (n) => {
    const r = n.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
  };
  const expanded = () => el.getAttribute('aria-expanded') === 'true';
  const optionsNow = () => {
    // Prefer the listbox the combobox points at; fall back to any visible
    // option (menus often portal elsewhere). Visibility filtering keeps other
    // widgets' mounted-but-hidden lists (intl-tel-input) out of the pool.
    const ctl = (el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '').split(/\s+/)[0];
    if (ctl) {
      const box = doc.getElementById(ctl);
      if (box) {
        const own = [...box.querySelectorAll('[role="option"]')].filter(shown);
        if (own.length) return own;
      }
    }
    return [...doc.querySelectorAll('[role="option"]')].filter(shown);
  };
  const comboVal = () => {
    try {
      const vc = el.closest('[class*="value-container"], [class*="ValueContainer"]');
      if (!vc) return '';
      const c = vc.cloneNode(true);
      for (const kill of c.querySelectorAll('input, [class*="placeholder"], [id$="placeholder"]')) kill.remove();
      return clip(c.textContent);
    } catch (e) { return ''; }
  };
"""

_COMBO_OPEN_JS = (
    r"""/*jc-act*/ /*jc-combo-open*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
"""
    + _COMBO_HELPERS_JS
    + r"""
  // Click target: the widget's control container (react-select opens on a
  // control mousedown), not the bare input. Scroll must be INSTANT — the page
  // may set scroll-behavior:smooth, and an animated scroll would make the
  // rect we report stale by the time the worker clicks it (observed live:
  // y=-369, the click sailed over the page).
  const ctl = el.closest('[class*="control"], [class*="Control"]') || el;
  try { ctl.scrollIntoView({ block: 'center', behavior: 'instant' }); } catch (e) {}
  const c = center(ctl);
  return JSON.stringify({ ok: true, x: c.x, y: c.y, open: expanded(), options: optionsNow().length });
})()"""
)

_COMBO_COUNT_JS = (
    r"""/*jc-act*/ /*jc-combo-count*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
"""
    + _COMBO_HELPERS_JS
    + r"""
  return JSON.stringify({ ok: true, options: optionsNow().length, open: expanded() });
})()"""
)

_COMBO_PICK_JS = (
    r"""/*jc-act*/ /*jc-combo-pick*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
  const want = String(__VALUE__);
"""
    + _COMBO_HELPERS_JS
    + r"""
  const opts = optionsNow();
  if (!opts.length) return JSON.stringify({ ok: false, error: 'combobox opened but no options appeared' });
  const fw = fold(want);
  const hit = opts.find((o) => fold(o.textContent) === fw)
    || opts.find((o) => fold(o.textContent).startsWith(fw))
    || opts.find((o) => fold(o.textContent).includes(fw))
    || opts.find((o) => {
      // Last resort: a short option ("Yes") stated as a word inside a wordy
      // value ("Yes, I can work from the office").
      const fo = fold(o.textContent);
      if (fo.length < 2 || fo.length > 40) return false;
      const esc = fo.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return new RegExp('(^|\\W)' + esc + '(\\W|$)').test(fw);
    });
  if (!hit) return JSON.stringify({
    ok: false, error: 'no option matches ' + want.slice(0, 60),
    options: opts.slice(0, 12).map((o) => clip(o.textContent)),
  });
  try { hit.scrollIntoView({ block: 'nearest', behavior: 'instant' }); } catch (e) {}
  const c = center(hit);
  return JSON.stringify({ ok: true, x: c.x, y: c.y, value: clip(hit.textContent) });
})()"""
)

_COMBO_VALUE_JS = (
    r"""/*jc-act*/ /*jc-combo-value*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
"""
    + _COMBO_HELPERS_JS
    + r"""
  return JSON.stringify({ ok: true, open: expanded(), value: comboVal() });
})()"""
)

_CHECK_JS = r"""/*jc-act*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
  const type = String(el.type || '').toLowerCase();
  if (type !== 'checkbox' && type !== 'radio')
    return JSON.stringify({ ok: false, error: 'not a checkbox/radio' });
  const on = __ON__;
  try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
  if (el.checked !== on) el.click();
  return JSON.stringify({ ok: true, checked: el.checked });
})()"""

# The mechanical no-submit core: submit-worded controls are refused ANYWHERE,
# submit-typed and wizard-worded controls are refused inside a <form> (a bare
# <button> in a form defaults to type=submit, so unmarked ATS buttons land on the
# safe side). Anchors never submit and reveal links stay clickable.
_CLICK_JS = r"""/*jc-act*/ (() => {
  const el = (window.__jcAF || {})[__REF__];
  if (!el) return JSON.stringify({ ok: false, error: 'stale ref — re-read the page' });
  const fold = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
  const text = fold((el.textContent || '') + ' ' + (el.value || '') + ' ' + (el.getAttribute('aria-label') || ''));
  const type = String(el.type || '').toLowerCase();
  const inForm = !!el.closest('form');
  if (new RegExp(__SUBMIT_RE__, 'i').test(text) || (inForm && (type === 'submit' || type === 'image')))
    return JSON.stringify({ ok: false, blocked: 'submit-guard', text: text.slice(0, 80) });
  if (inForm && new RegExp(__WIZARD_RE__, 'i').test(text))
    return JSON.stringify({ ok: false, blocked: 'wizard-guard', text: text.slice(0, 80) });
  // target=_blank apply links (WWR-style) would open the form in a tab this
  // session isn't driving — keep the navigation in the tab we control.
  if (el.tagName === 'A' && el.target) el.target = '_self';
  try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
  el.click();
  return JSON.stringify({ ok: true, clicked: text.slice(0, 80) });
})()"""

_REF_LOOKUP_JS = "/*jc-ref*/ (window.__jcAF || {})[__REF__]"


def _fill_js(ref: str, value: str) -> str:
    return _FILL_JS.replace("__REF__", json.dumps(ref)).replace(
        "__VALUE__", json.dumps(value)
    )


def _select_js(ref: str, value: str) -> str:
    return _SELECT_JS.replace("__REF__", json.dumps(ref)).replace(
        "__VALUE__", json.dumps(value)
    )


def _combo_open_js(ref: str) -> str:
    return _COMBO_OPEN_JS.replace("__REF__", json.dumps(ref))


def _combo_count_js(ref: str) -> str:
    return _COMBO_COUNT_JS.replace("__REF__", json.dumps(ref))


def _combo_pick_js(ref: str, value: str) -> str:
    return _COMBO_PICK_JS.replace("__REF__", json.dumps(ref)).replace(
        "__VALUE__", json.dumps(value)
    )


def _combo_value_js(ref: str) -> str:
    return _COMBO_VALUE_JS.replace("__REF__", json.dumps(ref))


def _check_js(ref: str, on: bool) -> str:
    return _CHECK_JS.replace("__REF__", json.dumps(ref)).replace(
        "__ON__", "true" if on else "false"
    )


def _click_js(ref: str) -> str:
    return (
        _CLICK_JS.replace("__REF__", json.dumps(ref))
        .replace("__SUBMIT_RE__", json.dumps(_SUBMIT_RE))
        .replace("__WIZARD_RE__", json.dumps(_WIZARD_RE))
    )


class AutofillStep(dspy.Signature):
    """Plan the next actions to PRE-FILL a job application form for the candidate.

    You are looking at one snapshot of the application page (``page``: its visible
    form controls with ``ref`` ids, clickable buttons/links, headings). Emit the
    actions to fill it from the candidate ``profile``. After your actions execute
    the page is re-read and you are called again (bounded rounds), so act on what
    is visible now — ``context`` lists what already ran; do not repeat actions
    that succeeded.

    HARD RULES:
      * NEVER submit the application. There is no submit action, and the runtime
        refuses clicks on submit buttons and on next/continue wizard advances —
        do not attempt them. The human reviews and submits.
      * Fill only from facts in the profile. Never invent employment history,
        references, IDs, or numbers. Leave a field blank rather than guessing.
      * Demographic / EEO survey questions: answer only when the profile's
        operator-provided application answers state the fact; otherwise pick the
        "decline to answer" / "prefer not to say" option when one exists, else
        skip the question.
      * Controls marked ``"combobox": true`` are dropdown widgets, not text
        fields: use select with the option's visible text (short, e.g. "Yes" —
        never a sentence). fill is refused on them. A failed select reports the
        widget's real option labels; retry next round with one of those.
      * Free-text motivation or cover-letter fields: write 2–4 specific,
        first-person sentences grounded in the profile and the role.
      * If the page shows no form controls yet, click the apply button/link that
        reveals or navigates to the form (and dismiss cookie banners).
      * NEVER put "done" after a "click" in the same array — you have not seen
        what the click revealed. End the array at the click; the refreshed page
        arrives next round. (A "done" following a click is deferred, not honored.)

    Actions (JSON array, executed in order):
      {"op": "fill",   "ref": "c3", "value": "text to type"}
      {"op": "select", "ref": "c7", "value": "option value or visible text"}
        — for <select> controls AND for "combobox": true inputs
      {"op": "check",  "ref": "c9", "on": true}
      {"op": "click",  "ref": "b2", "why": "short reason"}
      {"op": "upload", "ref": "c4"}                  — resume file input; only
        when context says a resume file is available (the runtime attaches the
        operator's configured file; you cannot choose a path)
      {"op": "done", "summary": "what was filled; what is left for the human"}

    Output ONLY the JSON array, no commentary. Emit "done" once everything
    fillable on this page is handled — alone, or after fill/select/check/upload
    actions (never after a click).
    """

    profile: str = dspy.InputField(
        desc="the candidate: contact facts, background, preferences, application answers"
    )
    context: str = dspy.InputField(
        desc="the position being applied to, the board it was found on, "
        "resume-file availability, and actions already executed"
    )
    page: str = dspy.InputField(
        desc="JSON snapshot of the page: form controls (ref ids), buttons/links, headings"
    )
    actions: str = dspy.OutputField(
        desc='JSON array of {"op", "ref", ...} action objects, ending with a "done" op when finished'
    )


def _parse_actions(raw: str) -> list[dict[str, Any]]:
    """Tolerantly pull the JSON action array out of a completion (fences, prose)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [a for a in parsed if isinstance(a, dict)][:_MAX_ACTIONS_PER_ROUND]


def _history_line(action: dict[str, Any], result: dict[str, Any]) -> str:
    op = str(action.get("op", "?"))
    ref = str(action.get("ref", ""))
    if result.get("ok"):
        outcome = "ok"
    elif result.get("blocked"):
        outcome = f"REFUSED ({result['blocked']})"
    else:
        outcome = f"failed: {str(result.get('error', '?'))[:80]}"
        options = result.get("options")
        if isinstance(options, list) and options:
            # A missed select reports the widget's real choices — feed them back
            # so the next round retries with an option that actually exists.
            listed = ", ".join(repr(str(o)[:40]) for o in options[:8])
            outcome += f" (options: {listed})"
    detail = str(action.get("value") or action.get("why") or "")[:60]
    return f"  - {op} {ref}{f' {detail!r}' if detail else ''} -> {outcome}"


class AutofillWorker:
    """Claims queued §18 runs and drives one prefill at a time (one browser, one form).

    All CDP I/O is async on the daemon's loop; the LLM call runs on a worker thread
    via ``asyncio.to_thread`` (the ``build_lm`` + ``dspy.context`` shape from
    :mod:`jobcrawler.match`). ``browser`` is injectable for tests; production keeps
    one :class:`CdpBrowser` across runs so Chrome's "Allow debugging" consent fires
    once per daemon lifetime.
    """

    def __init__(
        self,
        store: StateStore,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        max_rounds: int | None = None,
        cdp_url: str | None = None,
        profile_path: str | None = None,
        notes_path: str | None = None,
        resume_path: str | None = None,
        sites_path: str | None = None,
        browser: CdpBrowser | None = None,
    ) -> None:
        self._store = store
        self._model = model or config.require_autofill_model()
        self._max_tokens = (
            max_tokens if max_tokens is not None else config.autofill_max_tokens()
        )
        self._max_rounds = (
            max_rounds if max_rounds is not None else config.autofill_max_rounds()
        )
        self._profile_path = profile_path or config.profile_path()
        self._notes_path = (
            notes_path if notes_path is not None else config.autofill_notes_path()
        )
        self._resume_path = (
            resume_path if resume_path is not None else config.autofill_resume_path()
        )
        self._sites_path = sites_path if sites_path is not None else config.sites_path()
        self._browser = browser or CdpBrowser(cdp_url or config.autofill_cdp_url())
        # One predictor reused across runs; the LM is bound per call via dspy.context
        # (temperature 0), mirroring Matcher. Tests overwrite with a fake predictor.
        self._predict = dspy.Predict(AutofillStep)

    async def run_once(self) -> dict[str, object] | None:
        """Claim and execute one queued run; None when the queue is empty.

        Never raises: every failure lands on the run row (status ``failed`` + the
        transcript gathered so far) so the drain loop and the UI both see it.
        """
        req = self._store.claim_autofill()
        if req is None:
            return None
        run_id = int(str(req["id"]))
        site, url = str(req["site"]), str(req["url"])
        transcript: list[dict[str, object]] = []
        counters = {"rounds": 0, "actions": 0, "fields": 0}
        profile_hash: str | None = None
        try:
            profile_text, profile_hash = self._load_profile_text()
            summary = await self._drive(req, profile_text, transcript, counters)
            self._store.finish_autofill(
                run_id,
                status="done",
                summary=summary,
                model=self._model,
                profile_hash=profile_hash,
                rounds=counters["rounds"],
                actions_applied=counters["actions"],
                fields_filled=counters["fields"],
                transcript=transcript,
            )
            _log.info(
                "autofill done site=%s rounds=%d actions=%d fields=%d",
                site,
                counters["rounds"],
                counters["actions"],
                counters["fields"],
            )
            return {"id": run_id, "status": "done", "site": site, "url": url}
        except TabClosed:
            _log.info(
                "autofill run %d (%s): apply tab closed by the operator — interrupted",
                run_id,
                site,
            )
            self._store.finish_autofill(
                run_id,
                status="failed",
                error="interrupted: apply tab closed in Chrome",
                model=self._model,
                profile_hash=profile_hash,
                rounds=counters["rounds"],
                actions_applied=counters["actions"],
                fields_filled=counters["fields"],
                transcript=transcript,
            )
            return {"id": run_id, "status": "failed", "site": site, "url": url}
        except Exception as exc:  # noqa: BLE001 - outcome lands on the run row
            _log.exception("autofill run %d (%s) failed", run_id, site)
            self._store.finish_autofill(
                run_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
                model=self._model,
                profile_hash=profile_hash,
                rounds=counters["rounds"],
                actions_applied=counters["actions"],
                fields_filled=counters["fields"],
                transcript=transcript,
            )
            return {"id": run_id, "status": "failed", "site": site, "url": url}

    # -- the look→plan→act loop ------------------------------------------------

    async def _drive(
        self,
        req: dict[str, object],
        profile_text: str,
        transcript: list[dict[str, object]],
        counters: dict[str, int],
    ) -> str:
        target = str(req["apply_url"])
        rec = self._store.get_position(str(req["site"]), str(req["url"]))
        if rec is not None:
            fresh = autofill_target(rec)
            if fresh:
                target = fresh  # the row may have gained a better apply_url since enqueue
        board = self._board_label(str(req["site"]))
        if not self._browser.connected:
            await self._browser.connect()
        target_id = await self._browser.open_tab(target)
        sid = await self._browser.attach(target_id)
        await self._browser.cmd("Page.enable", session_id=sid)
        await self._browser.cmd("Runtime.enable", session_id=sid)
        await self._browser.cmd("DOM.enable", session_id=sid)
        await self._wait_ready(sid, target_id)

        history: list[str] = []
        summary = "round budget exhausted before the model finished"
        mine = {target_id}  # tabs this run drives; grows when the page opens one
        for rnd in range(1, self._max_rounds + 1):
            counters["rounds"] = rnd
            try:
                digest = await self._sweep(sid)
            except CdpError:
                if not await self._tab_alive(target_id):
                    raise TabClosed("apply tab closed in Chrome") from None
                raise
            entry: dict[str, object] = {"round": rnd, "page": digest, "results": []}
            transcript.append(entry)
            raw = await asyncio.to_thread(
                self._call_llm,
                profile_text,
                self._context(rec, target, history, board),
                json.dumps(digest, ensure_ascii=False)[:_PAGE_JSON_BUDGET],
            )
            entry["llm_raw"] = raw
            actions = _parse_actions(raw)
            if not actions:
                summary = "model returned no parseable actions"
                break
            results: list[dict[str, object]] = entry["results"]  # type: ignore[assignment]
            done: str | None = None
            navigated = False
            any_ok = False
            for action in actions:
                if str(action.get("op", "")).lower() == "done":
                    if navigated:
                        # The model is declaring done for a page it hasn't seen:
                        # a click just ran this round and may have navigated or
                        # revealed the form (BambooHR modal, WWR→Breezy hop).
                        # Defer — next round plans against the fresh sweep.
                        results.append(
                            {
                                "action": action,
                                "result": {
                                    "ok": False,
                                    "deferred": "done after a click in the same "
                                    "round — the page is re-read first",
                                },
                            }
                        )
                        history.append(
                            "  - done -> DEFERRED (a click ran this round; "
                            "plan again from the fresh page)"
                        )
                        summary = str(action.get("summary") or summary)
                        break
                    done = str(action.get("summary") or "prefill complete")
                    break
                result = await self._act(sid, action)
                results.append({"action": action, "result": result})
                history.append(_history_line(action, result))
                counters["actions"] += 1
                if result.get("ok"):
                    any_ok = True
                    op = str(action.get("op", "")).lower()
                    if op in ("fill", "select", "check", "upload"):
                        counters["fields"] += 1
                    if op == "click":
                        navigated = True  # a click may navigate or re-render
                await asyncio.sleep(0.15)  # let the page's JS react between actions
            if done is not None:
                summary = done
                break
            if not any_ok:
                # A closed tab fails every action the same way — check before
                # calling it a stall.
                if not await self._tab_alive(target_id):
                    raise TabClosed("apply tab closed in Chrome")
                summary = f"stalled: no action succeeded in round {rnd}"
                break
            if navigated:
                await asyncio.sleep(_POST_CLICK_SETTLE_S)
                adopted = await self._adopt_popup(mine)
                if adopted is not None:
                    target_id, sid, popup_url = adopted
                    mine.add(target_id)
                    entry["popup"] = {"target_id": target_id, "url": popup_url}
                    history.append(
                        f"  - (the page opened a new tab; now driving it: {popup_url})"
                    )
                await self._wait_ready(sid, target_id)  # bounded; covers navigations
            else:
                await asyncio.sleep(1.0)  # async validation / dependent fields settle
        # The tab is deliberately left open: the operator reviews and submits.
        return summary

    def _call_llm(self, profile: str, context: str, page: str) -> str:
        lm = build_lm(self._model, temperature=0.0, max_tokens=self._max_tokens)
        with dspy.context(lm=lm):
            prediction = self._predict(profile=profile, context=context, page=page)
        return str(prediction.actions)

    def _context(
        self,
        rec: PositionRecord | None,
        target: str,
        history: list[str],
        board: str | None = None,
    ) -> str:
        parts: list[str] = []
        if rec is not None:
            parts.append(
                f"Applying to: {rec.title} at {rec.hiring_company or rec.company}"
            )
        if board:
            parts.append(
                f"Found via: {board}. When the form asks how you heard about the "
                "role/company, answer with this (pick the closest offered option)."
            )
        parts.append(f"Application page: {target}")
        parts.append(
            "Resume file: available — use the upload op on the resume/CV file input"
            if self._resume_available()
            else "Resume file: NOT configured — leave file inputs for the human"
        )
        if history:
            parts.append(
                "Actions already executed this run (do not repeat successful ones):"
            )
            parts.extend(history[-60:])
        else:
            parts.append("No actions executed yet this run.")
        return "\n".join(parts)

    def _resume_available(self) -> bool:
        return bool(self._resume_path) and os.path.isfile(str(self._resume_path))

    def _board_label(self, slug: str) -> str | None:
        """Where the operator found this position — the "how did you hear" answer.

        A single-company board (registry ``company`` set) is the employer's own
        posting, so the truthful answer is their careers site; an aggregator
        answers with its registry name. Fail-soft None (unreadable registry,
        retired slug): the context line is dropped rather than failing the run.
        """
        try:
            site = find_site(load_sites(self._sites_path), slug)
        except (FileNotFoundError, yaml.YAMLError, ValidationError, OSError):
            return None
        if site is None:
            return None
        if site.company:
            return "the company's own careers site"
        return f"the {site.name or slug} job board"

    def _load_profile_text(self) -> tuple[str, str]:
        """The prefill context: PROFILE.md verbatim + optional APPLY.md answers."""
        prof = load_profile(self._profile_path)
        text = prof.raw.strip()
        if self._notes_path and os.path.isfile(self._notes_path):
            try:
                with open(self._notes_path, encoding="utf-8") as fh:
                    notes = fh.read().strip()
            except OSError:
                notes = ""
            if notes:
                text += (
                    "\n\n# Application answers (operator-provided; prefer these "
                    "for form-only facts like phone/links/salary)\n" + notes
                )
        return text[:_PROFILE_BUDGET], prof.profile_hash

    # -- CDP page interaction ----------------------------------------------------

    async def _wait_ready(self, sid: str, target_id: str) -> None:
        """Navigation + SPA settle: readyState on a real URL, then (bounded) form controls.

        ATS boards render client-side, so 'complete' alone is too early; a page that
        never grows controls (a reveal-button landing page) just times the second
        loop out and proceeds — the LLM's first round handles it with a click.
        A CDP error here is either a mid-navigation race (keep polling) or the
        operator closing the tab (raise :class:`TabClosed` instead of spinning
        out the full budget against a dead session).
        """
        for _ in range(60):  # ≤30s for the navigation itself
            try:
                state = await self._browser.evaluate(sid, _READY_JS)
            except CdpError:
                if not await self._tab_alive(target_id):
                    raise TabClosed("apply tab closed in Chrome") from None
                state = None
            if state == "complete":
                break
            await asyncio.sleep(0.5)
        for _ in range(16):  # ≤8s for a client-side form; proceed regardless
            try:
                count = await self._browser.evaluate(sid, _CONTROL_COUNT_JS)
            except CdpError:
                if not await self._tab_alive(target_id):
                    raise TabClosed("apply tab closed in Chrome") from None
                count = 0
            if isinstance(count, (int, float)) and count > 0:
                return
            await asyncio.sleep(0.5)

    async def _tab_alive(self, target_id: str) -> bool:
        """Does the tab still exist? Closing it is the operator's interrupt.

        ``Target.getTargetInfo`` is browser-scoped and version-stable — a closed
        tab raises, everything else answers — so no fragile matching on CDP
        error strings.
        """
        try:
            await self._browser.cmd("Target.getTargetInfo", {"targetId": target_id})
        except CdpError:
            return False
        return True

    async def _adopt_popup(self, mine: set[str]) -> tuple[str, str, str] | None:
        """Adopt a tab the page opened from one of ours (window.open flows).

        The click executor rewrites anchor targets to ``_self``, but JS-driven
        apply buttons still spawn tabs — and then the form lives in a tab this
        session isn't driving. Returns ``(target_id, session_id, url)`` to switch
        to, or None to stay put. Best-effort: never raises. Only targets whose
        ``openerId`` is one of this run's tabs qualify, so tabs the operator
        opens by hand are never grabbed.
        """
        try:
            res = await self._browser.cmd("Target.getTargets")
        except CdpError:
            return None
        infos = res.get("targetInfos", [])
        popups = [
            t
            for t in infos
            if isinstance(t, dict)
            and t.get("type") == "page"
            and str(t.get("openerId") or "") in mine
            and str(t.get("targetId") or "") not in mine
        ]
        if not popups:
            return None
        chosen = popups[-1]  # newest when several appeared
        tid = str(chosen["targetId"])
        try:
            sid = await self._browser.attach(tid)
            await self._browser.cmd("Page.enable", session_id=sid)
            await self._browser.cmd("Runtime.enable", session_id=sid)
            await self._browser.cmd("DOM.enable", session_id=sid)
        except CdpError:
            return None
        return tid, sid, str(chosen.get("url") or "")

    async def _sweep(self, sid: str) -> dict[str, Any]:
        value = await self._browser.evaluate(sid, _SWEEP_JS)
        if not isinstance(value, str):
            raise CdpError("page sweep returned no JSON")
        digest = json.loads(value)
        if not isinstance(digest, dict):
            raise CdpError("page sweep returned a non-object")
        return digest

    async def _act(self, sid: str, action: dict[str, Any]) -> dict[str, Any]:
        """Execute one LLM action via the fixed JS executors; never raises."""
        op = str(action.get("op", "")).lower()
        ref = str(action.get("ref", ""))
        try:
            if op == "fill":
                return await self._eval_action(
                    sid, _fill_js(ref, str(action.get("value", "")))
                )
            if op == "select":
                result = await self._eval_action(
                    sid, _select_js(ref, str(action.get("value", "")))
                )
                if result.get("combo"):
                    return await self._combo_select(
                        sid, ref, str(action.get("value", ""))
                    )
                return result
            if op == "check":
                return await self._eval_action(
                    sid, _check_js(ref, bool(action.get("on", True)))
                )
            if op == "click":
                return await self._eval_action(sid, _click_js(ref))
            if op == "upload":
                return await self._upload(sid, ref)
            return {"ok": False, "error": f"unknown op {op!r}"}
        except CdpError as exc:
            return {"ok": False, "error": str(exc)[:300]}

    # Combobox menu polling: options usually mount within one React render of
    # the open click; async typeaheads (location search) need network time.
    # The waits live HERE, in the worker — page timers in a backgrounded tab
    # are throttled to ~one wake per minute, so any in-page polling would hang.
    _COMBO_POLLS = 12
    _COMBO_TYPED_POLLS = 16
    _COMBO_POLL_S = 0.15

    async def _combo_select(self, sid: str, ref: str, want: str) -> dict[str, Any]:
        """Commit a choice in a React/ARIA combobox (Greenhouse job-boards etc.).

        Two hard-won facts drive this shape. A choice commits ONLY by clicking
        a rendered [role=option] — writing the input's value "succeeds" and the
        form stays empty (run 7). And the clicks/keys must be TRUSTED CDP Input
        events: Greenhouse's React build ignores dispatchEvent-synthesized
        events wholesale (verified live — full propagation, zero handler
        response; a trusted click opened the menu instantly). So page JS only
        locates things and reports coordinates; this method does the touching.
        """
        opened = await self._eval_action(sid, _combo_open_js(ref))
        if not opened.get("ok"):
            return opened
        if not opened.get("open"):
            await self._trusted_click(sid, opened["x"], opened["y"])
        typed = False
        count = int(opened.get("options") or 0)
        for _ in range(self._COMBO_POLLS):
            if count:
                break
            await asyncio.sleep(self._COMBO_POLL_S)
            res = await self._eval_action(sid, _combo_count_js(ref))
            if not res.get("ok"):
                return res
            count = int(res.get("options") or 0)
        if not count:
            # Async typeahead: no options until the query text arrives. The
            # open click focused the input; insertText types like a real IME.
            typed = True
            await self._browser.cmd(
                "Input.insertText", {"text": want}, session_id=sid
            )
            for _ in range(self._COMBO_TYPED_POLLS):
                await asyncio.sleep(self._COMBO_POLL_S)
                res = await self._eval_action(sid, _combo_count_js(ref))
                if not res.get("ok"):
                    return res
                count = int(res.get("options") or 0)
                if count:
                    break
        if not count:
            await self._combo_settle(sid, ref)
            return {"ok": False, "error": "combobox opened but no options appeared"}
        picked = await self._eval_action(sid, _combo_pick_js(ref, want))
        if not picked.get("ok"):
            await self._combo_settle(sid, ref)
            return picked
        await self._trusted_click(sid, picked["x"], picked["y"])
        # A committed pick normally closes the menu; multi-selects keep it
        # open — settle and read back what the widget actually shows now.
        await asyncio.sleep(self._COMBO_POLL_S)
        committed = await self._combo_settle(sid, ref)
        result: dict[str, Any] = {"ok": True, "value": picked.get("value")}
        if committed and typed:
            result["value"] = committed
        return result

    async def _combo_settle(self, sid: str, ref: str) -> str:
        """Close a still-open menu (trusted Escape) and read the shown value."""
        res = await self._eval_action(sid, _combo_value_js(ref))
        if res.get("open"):
            await self._press_escape(sid)
        return str(res.get("value") or "")

    async def _trusted_click(self, sid: str, x: float, y: float) -> None:
        """A real left click through the browser's input pipeline (isTrusted)."""
        for kind in ("mousePressed", "mouseReleased"):
            await self._browser.cmd(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
                session_id=sid,
            )

    async def _press_escape(self, sid: str) -> None:
        for kind in ("keyDown", "keyUp"):
            await self._browser.cmd(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": "Escape",
                    "code": "Escape",
                    "windowsVirtualKeyCode": 27,
                },
                session_id=sid,
            )

    async def _eval_action(self, sid: str, js: str) -> dict[str, Any]:
        value = await self._browser.evaluate(sid, js)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"ok": False, "error": "executor returned no result"}

    async def _upload(self, sid: str, ref: str) -> dict[str, Any]:
        """Attach the operator-configured resume to a file input.

        The one action that goes through CDP rather than page JS
        (``DOM.setFileInputFiles`` needs the element's objectId) — and the path is
        ALWAYS the configured one; the LLM's action carries no filename.
        """
        path = self._resume_path
        if not path or not os.path.isfile(path):
            return {
                "ok": False,
                "error": "no resume file configured (JOBCRAWLER_AUTOFILL_RESUME)",
            }
        res = await self._browser.cmd(
            "Runtime.evaluate",
            {
                "expression": _REF_LOOKUP_JS.replace("__REF__", json.dumps(ref)),
                "returnByValue": False,
            },
            session_id=sid,
        )
        obj = res.get("result", {})
        object_id = obj.get("objectId") if isinstance(obj, dict) else None
        if not object_id:
            return {"ok": False, "error": "stale ref — re-read the page"}
        await self._browser.cmd(
            "DOM.setFileInputFiles",
            {"files": [os.path.abspath(path)], "objectId": object_id},
            session_id=sid,
        )
        return {"ok": True, "value": os.path.basename(path)}
