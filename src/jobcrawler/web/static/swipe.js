// Swipe-triage deck (§16). One card at a time over /api/swipe/queue; a decision
// posts to /api/swipe/{pid} (h/left = dismiss, l/right = the guarded one-way
// new→interested promotion). Everything here is presentation + optimistic
// bookkeeping — policy (what a decision may write) lives server-side.
//
// Input surfaces, all driving the same three verbs (next/prev, pass, like):
//   keys      j / k navigate, h pass, l interested, u undo, o posting, enter detail
//   pointer   drag the top card past the threshold (mouse or touch)
//   touchpad  two-finger horizontal scroll accumulates into the same drag
(function () {
  "use strict";

  const stage = document.getElementById("swipe");
  if (!stage) return;

  const deckEl = document.getElementById("swipe-deck");
  const emptyEl = document.getElementById("swipe-empty");
  const emptyTitle = document.getElementById("empty-title");
  const emptyStats = document.getElementById("empty-stats");
  const loadingEl = document.getElementById("swipe-loading");
  const reloadBtn = document.getElementById("swipe-reload");
  const undoBtn = document.getElementById("swipe-undo");
  const hud = {
    pos: document.getElementById("hud-pos"),
    total: document.getElementById("hud-total"),
    bar: document.getElementById("hud-bar"),
    passed: document.getElementById("hud-passed"),
    liked: document.getElementById("hud-liked"),
    streak: document.getElementById("hud-streak"),
  };

  const QUEUE_URL = stage.dataset.queueUrl;
  const DECIDE_URL = stage.dataset.decideUrl;

  const VISIBLE_DEPTH = 3; // top card + this many peeking behind it
  const REFILL_LOW_WATER = 12; // refill when fewer undealt cards remain
  const REFILL_MAX_PAGES = 8; // bound the page walk per refill pass

  // ---- state ---------------------------------------------------------------

  const state = {
    cards: [], // undecided cards in deck order; cursor indexes into this
    cursor: 0,
    seen: new Set(), // every pid dealt this session (dedupe across refills)
    liked: 0,
    passed: 0,
    streak: 0, // consecutive same-direction decisions (HUD flourish)
    streakDir: null,
    history: [], // undo stack: {card, index, decision, commit, changed}
    fetching: false,
    exhausted: false, // the server ran out of pages for this query
    failed: false, // initial fetch failed (empty state shows a retry)
    query: stage.dataset.query || "",
  };

  const els = new Map(); // pid -> card element, kept across renders for transitions

  const toast = (msg, opts) =>
    typeof Toast !== "undefined" ? Toast.show(msg, opts) : undefined;

  // ---- queue fetch / refill --------------------------------------------------

  async function fetchQueue(page) {
    const params = new URLSearchParams(state.query);
    params.delete("page");
    if (page > 1) params.set("page", String(page));
    const qs = params.toString();
    const r = await fetch(QUEUE_URL + (qs ? "?" + qs : ""), {
      credentials: "same-origin",
    });
    if (!r.ok) throw new Error("queue fetch failed: " + r.status);
    return r.json();
  }

  // Refill by walking pages from 1: decisions shrink the default query, so page 1
  // usually refills by itself; on a query decisions DON'T leave (any-stage,
  // show_dismissed) every fetched pid is already seen and we walk further in.
  async function refill(initial) {
    if (state.fetching || (state.exhausted && !initial)) return;
    state.fetching = true;
    if (initial) {
      loadingEl.hidden = false;
      emptyEl.hidden = true;
    }
    try {
      let page = 1;
      let hasNext = true;
      let added = 0;
      while (added < REFILL_LOW_WATER && hasNext && page <= REFILL_MAX_PAGES) {
        const body = await fetchQueue(page);
        hasNext = Boolean(body.has_next);
        for (const card of body.positions) {
          if (state.seen.has(card.pid)) continue;
          state.seen.add(card.pid);
          state.cards.push(card);
          added += 1;
        }
        page += 1;
      }
      state.exhausted = !hasNext;
      state.failed = false;
    } catch (err) {
      if (initial) state.failed = true;
      toast("Couldn't load the deck — is the server up?");
    } finally {
      state.fetching = false;
      loadingEl.hidden = true;
    }
    render();
  }

  function maybeRefill() {
    if (!state.exhausted && state.cards.length - state.cursor < REFILL_LOW_WATER) {
      refill(false);
    }
  }

  // ---- card DOM --------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function chip(text, className) {
    return el("span", "card-chip" + (className ? " " + className : ""), text);
  }

  function locationLine(card) {
    return card.location_display || card.location_normalized || card.location || "";
  }

  function buildCard(card) {
    const root = el("article", "swipe-card band-" + (card.band || "none"));
    root.dataset.pid = card.pid;
    root.style.setProperty("--hue", String(card.hue || 210));

    // decision stamps (opacity driven by drag distance)
    root.appendChild(el("div", "stamp stamp-like", "INTERESTED"));
    root.appendChild(el("div", "stamp stamp-pass", "PASS"));

    const inner = el("div", "card-inner");
    root.appendChild(inner);

    // header: avatar · company/site/age · score
    const head = el("header", "card-head");
    const avatar = el("span", "card-avatar", (card.hiring_company || card.company || "·").slice(0, 1).toUpperCase());
    head.appendChild(avatar);
    const who = el("div", "card-who");
    who.appendChild(el("div", "card-company", card.hiring_company || card.company || "Unknown company"));
    who.appendChild(el("div", "card-meta", [card.site, card.age].filter(Boolean).join(" · ")));
    head.appendChild(who);
    const score = el("div", "card-score score-" + (card.band || "none"), card.score === null || card.score === undefined ? "—" : String(card.score));
    score.title = "match score";
    head.appendChild(score);
    inner.appendChild(head);

    inner.appendChild(el("h2", "card-title", card.title || "Untitled position"));

    // classification chips
    const chips = el("div", "card-chips");
    if (card.level_label) chips.appendChild(chip(card.level_label));
    if (card.discipline_label) chips.appendChild(chip(card.discipline_label));
    if (card.workplace_label) chips.appendChild(chip(card.workplace_label, "chip-workplace"));
    if (card.remote_scope === "anywhere") chips.appendChild(chip("Anywhere", "chip-good"));
    if (card.remote_scope === "restricted") chips.appendChild(chip("Geo-restricted", "chip-warn"));
    if (card.relocation === "offered") chips.appendChild(chip("Relocation", "chip-good"));
    if (card.employment_type) chips.appendChild(chip(card.employment_type));
    if (card.status === "closed") chips.appendChild(chip("Closed", "chip-warn"));
    if (chips.children.length) inner.appendChild(chips);

    // the two facts that decide most swipes: money and place
    const facts = el("div", "card-facts");
    if (card.salary) facts.appendChild(el("div", "card-salary", card.salary));
    const loc = locationLine(card);
    if (loc) facts.appendChild(el("div", "card-location", loc));
    if (facts.children.length) inner.appendChild(facts);

    // why the matcher liked / worried
    const reasons = (card.match_reasons || []).slice(0, 4);
    const concerns = (card.match_concerns || []).slice(0, 3);
    if (reasons.length || concerns.length) {
      const match = el("div", "card-match");
      for (const r of reasons) {
        const line = el("div", "match-line match-pro");
        line.appendChild(el("span", "match-mark", "✓"));
        line.appendChild(el("span", "", r));
        match.appendChild(line);
      }
      for (const cn of concerns) {
        const line = el("div", "match-line match-con");
        line.appendChild(el("span", "match-mark", "⚠"));
        line.appendChild(el("span", "", cn));
        match.appendChild(line);
      }
      inner.appendChild(match);
    }

    if (card.description) {
      const desc = el("div", "card-desc");
      desc.appendChild(el("p", "", card.description));
      inner.appendChild(desc);
    }

    // footer: explicit buttons for pointer users + the two links
    const foot = el("footer", "card-foot");
    const pass = el("button", "card-btn btn-pass", "✕ Pass");
    pass.type = "button";
    pass.title = "Dismiss (h / swipe left)";
    pass.addEventListener("click", () => decide("dismiss"));
    foot.appendChild(pass);

    const links = el("div", "card-links");
    if (card.ext) {
      const ext = el("a", "card-link", "posting ↗");
      ext.href = card.ext;
      ext.target = "_blank";
      ext.rel = "noopener noreferrer";
      links.appendChild(ext);
    }
    const detail = el("a", "card-link", "details");
    detail.href = card.detail_url;
    detail.target = "_blank";
    detail.rel = "noopener";
    links.appendChild(detail);
    foot.appendChild(links);

    const like = el("button", "card-btn btn-like", "★ Interested");
    like.type = "button";
    like.title = "Mark interested (l / swipe right)";
    like.addEventListener("click", () => decide("interested"));
    foot.appendChild(like);
    root.appendChild(foot);

    return root;
  }

  // ---- render ----------------------------------------------------------------

  function render() {
    const { cards, cursor } = state;

    // keep only elements for cards in the rendered window: one behind the
    // cursor (so k can slide it back down) through VISIBLE_DEPTH ahead
    const lo = Math.max(0, cursor - 1);
    const hi = Math.min(cards.length, cursor + VISIBLE_DEPTH + 1);
    const keep = new Set();
    for (let i = lo; i < hi; i++) keep.add(cards[i].pid);
    for (const [pid, node] of els) {
      if (!keep.has(pid) && !node.classList.contains("flying")) {
        node.remove();
        els.delete(pid);
      }
    }

    for (let i = lo; i < hi; i++) {
      const card = cards[i];
      let node = els.get(card.pid);
      if (!node) {
        node = buildCard(card);
        node.classList.add("dealt");
        els.set(card.pid, node);
        deckEl.appendChild(node);
        requestAnimationFrame(() => node.classList.remove("dealt"));
      }
      const depth = i - cursor;
      node.classList.toggle("is-top", depth === 0);
      node.classList.toggle("is-behind", depth < 0);
      node.style.setProperty("--i", String(Math.max(0, depth)));
      node.style.zIndex = String(100 - depth);
    }

    const remaining = cards.length - cursor;
    const decided = state.liked + state.passed;
    hud.pos.textContent = remaining > 0 ? String(cursor + 1) : "–";
    hud.total.textContent = cards.length ? String(cards.length) + (state.exhausted ? "" : "+") : state.exhausted ? "0" : "–";
    const denom = decided + cards.length;
    hud.bar.style.width = denom ? (100 * decided) / denom + "%" : "0%";
    hud.passed.textContent = String(state.passed);
    hud.liked.textContent = String(state.liked);
    undoBtn.disabled = !state.history.length;

    if (state.streak >= 3) {
      hud.streak.hidden = false;
      hud.streak.textContent =
        "×" + state.streak + " " + (state.streakDir === "interested" ? "★" : "✕");
      hud.streak.className = "hud-streak streak-" + (state.streakDir === "interested" ? "like" : "pass");
    } else {
      hud.streak.hidden = true;
    }

    const empty = remaining <= 0 && !state.fetching;
    emptyEl.hidden = !empty;
    if (empty) {
      if (state.failed) {
        emptyTitle.textContent = "Couldn't load the deck";
        emptyStats.textContent = "The server didn't answer — retry in a moment.";
        reloadBtn.textContent = "Retry";
      } else {
        emptyTitle.textContent = decided ? "Deck cleared" : "Nothing to triage";
        emptyStats.textContent = decided
          ? "★ " + state.liked + " interested · ✕ " + state.passed + " passed"
          : "No positions match this filter right now.";
        reloadBtn.textContent = "Deal again";
      }
    }
  }

  // ---- decisions ---------------------------------------------------------------

  function topCard() {
    return state.cursor < state.cards.length ? state.cards[state.cursor] : null;
  }

  function topEl() {
    const card = topCard();
    return card ? els.get(card.pid) : null;
  }

  function flyOff(node, dir) {
    if (!node) return;
    // free the pid immediately — an undo inside the animation window must be
    // able to rebuild a fresh element for the same card
    els.delete(node.dataset.pid);
    const sign = dir === "interested" ? 1 : -1;
    node.classList.remove("dragging"); // restore the transition
    node.classList.add("flying", dir === "interested" ? "fly-right" : "fly-left");
    node.style.zIndex = "200";
    // inline, because a drag already left an inline transform behind
    node.style.transform =
      "translate(" +
      sign * (window.innerWidth * 0.6 + 240) +
      "px, " +
      (Math.random() * 120 - 80) +
      "px) rotate(" +
      sign * 26 +
      "deg)";
    node.style.opacity = "0";
    window.setTimeout(() => node.remove(), 420);
  }

  function decide(decision) {
    const card = topCard();
    if (!card) return;
    const node = topEl();
    flyOff(node, decision);

    const index = state.cursor;
    state.cards.splice(index, 1);
    // deciding the last card lands the cursor back on the skipped pile, so a
    // j-j-j…-h run still finishes the whole deck instead of dead-ending
    state.cursor = Math.min(state.cursor, Math.max(0, state.cards.length - 1));
    if (decision === "interested") state.liked += 1;
    else state.passed += 1;
    state.streak = state.streakDir === decision ? state.streak + 1 : 1;
    state.streakDir = decision;

    const entry = { card, index, decision, changed: false, commit: null };
    entry.commit = fetch(DECIDE_URL + "/" + encodeURIComponent(card.pid), {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ decision }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("decision failed: " + r.status);
        return r.json();
      })
      .then((body) => {
        entry.changed = Boolean(body.changed);
      })
      .catch(() => {
        // roll the optimistic move back and put the card where it was
        const at = Math.min(index, state.cards.length);
        state.cards.splice(at, 0, card);
        if (decision === "interested") state.liked -= 1;
        else state.passed -= 1;
        state.streak = 0;
        state.streakDir = null;
        const i = state.history.indexOf(entry);
        if (i >= 0) state.history.splice(i, 1);
        render();
        toast("Couldn't save that swipe — the card is back in the deck");
      });
    state.history.push(entry);

    render();
    maybeRefill();
  }

  function undo() {
    const entry = state.history.pop();
    if (!entry) return;
    // wait for the decision's own write to settle, then only revert what we
    // actually changed (changed:false = the server never wrote anything)
    Promise.resolve(entry.commit).then(() => {
      if (entry.changed) {
        const revert =
          entry.decision === "dismiss"
            ? {
                url: "/api/positions/dismiss",
                body: { site: entry.card.site, url: entry.card.url, dismissed: false },
              }
            : {
                url: "/api/positions/track",
                body: { site: entry.card.site, url: entry.card.url, track_status: "new" },
              };
        fetch(revert.url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(revert.body),
        }).catch(() => toast("Couldn't undo on the server — check " + entry.card.title));
      }
    });

    const at = Math.min(entry.index, state.cards.length);
    state.cards.splice(at, 0, entry.card);
    state.cursor = at;
    if (entry.decision === "interested") state.liked = Math.max(0, state.liked - 1);
    else state.passed = Math.max(0, state.passed - 1);
    state.streak = 0;
    state.streakDir = null;
    render();
  }

  function next() {
    if (state.cursor < state.cards.length - 1) {
      state.cursor += 1;
      render();
      maybeRefill();
    } else {
      bounce();
      maybeRefill();
    }
  }

  function prev() {
    if (state.cursor > 0) {
      state.cursor -= 1;
      render();
    } else {
      bounce();
    }
  }

  function bounce() {
    const node = topEl();
    if (!node) return;
    node.classList.remove("bounce");
    void node.offsetWidth; // restart the animation
    node.classList.add("bounce");
  }

  function openExt() {
    const card = topCard();
    if (card && card.ext) window.open(card.ext, "_blank", "noopener");
  }

  function openDetail() {
    const card = topCard();
    if (card) window.open(card.detail_url, "_blank", "noopener");
  }

  // ---- keyboard ----------------------------------------------------------------

  // Capture phase on window so the list-page bindings in app.js (same document)
  // never double-handle deck keys.
  window.addEventListener(
    "keydown",
    (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target instanceof Element && target.matches("input, select, textarea")) return;
      const actions = {
        j: next,
        k: prev,
        h: () => decide("dismiss"),
        l: () => decide("interested"),
        u: undo,
        z: undo,
        o: openExt,
        Enter: openDetail,
      };
      const action = actions[event.key];
      if (!action) return;
      event.preventDefault();
      event.stopPropagation();
      action();
    },
    true
  );

  // ---- pointer drag --------------------------------------------------------------

  // Threshold fractions of the card width: past COMMIT_AT (or flung faster than
  // FLING_V px/ms) the release commits; anything less springs back.
  const COMMIT_AT = 0.38;
  const FLING_V = 0.65;

  const drag = {
    active: false,
    engaged: false, // moved past the click slop
    pid: null,
    startX: 0,
    startY: 0,
    dx: 0,
    dy: 0,
    lastX: 0,
    lastT: 0,
    vx: 0,
  };

  function dragTarget() {
    const node = topEl();
    return node && !node.classList.contains("flying") ? node : null;
  }

  function setDrag(node, dx, dy, animate) {
    node.classList.toggle("dragging", !animate);
    node.style.transform =
      "translate(" + dx + "px, " + dy * 0.32 + "px) rotate(" + dx * 0.055 + "deg)";
    const strength = Math.min(1, Math.abs(dx) / (node.offsetWidth * COMMIT_AT));
    node.style.setProperty("--like", dx > 0 ? String(strength) : "0");
    node.style.setProperty("--pass", dx < 0 ? String(strength) : "0");
    stage.style.setProperty("--lean", String(Math.max(-1, Math.min(1, dx / 320))));
  }

  function clearDrag(node) {
    node.classList.remove("dragging");
    node.style.transform = "";
    node.style.setProperty("--like", "0");
    node.style.setProperty("--pass", "0");
    stage.style.setProperty("--lean", "0");
  }

  function releaseDrag(node) {
    const width = node.offsetWidth || 360;
    const past = Math.abs(drag.dx) > width * COMMIT_AT;
    const flung = Math.abs(drag.vx) > FLING_V && Math.abs(drag.dx) > 48;
    if (past || flung) {
      stage.style.setProperty("--lean", "0");
      decide(drag.dx > 0 ? "interested" : "dismiss");
    } else {
      clearDrag(node);
    }
  }

  deckEl.addEventListener("pointerdown", (event) => {
    const node = dragTarget();
    if (!node || !node.contains(event.target)) return;
    if (event.button !== 0) return;
    drag.active = true;
    drag.engaged = false;
    drag.pid = node.dataset.pid;
    drag.startX = event.clientX;
    drag.startY = event.clientY;
    drag.lastX = event.clientX;
    drag.lastT = event.timeStamp;
    drag.dx = 0;
    drag.dy = 0;
    drag.vx = 0;
  });

  deckEl.addEventListener("pointermove", (event) => {
    if (!drag.active) return;
    const node = dragTarget();
    if (!node || node.dataset.pid !== drag.pid) return;
    drag.dx = event.clientX - drag.startX;
    drag.dy = event.clientY - drag.startY;
    if (!drag.engaged) {
      if (Math.hypot(drag.dx, drag.dy) < 8) return; // click slop
      drag.engaged = true;
      deckEl.setPointerCapture(event.pointerId);
    }
    const dt = event.timeStamp - drag.lastT;
    if (dt > 0) {
      drag.vx = drag.vx * 0.8 + ((event.clientX - drag.lastX) / dt) * 0.2;
      drag.lastX = event.clientX;
      drag.lastT = event.timeStamp;
    }
    setDrag(node, drag.dx, drag.dy, false);
    event.preventDefault();
  });

  function endPointer() {
    if (!drag.active) return;
    const engaged = drag.engaged;
    drag.active = false;
    drag.engaged = false;
    const node = dragTarget();
    if (!node || node.dataset.pid !== drag.pid) return;
    if (engaged) releaseDrag(node);
  }

  deckEl.addEventListener("pointerup", endPointer);
  deckEl.addEventListener("pointercancel", endPointer);

  // Swallow the click that follows a real drag so a swipe never also follows a link.
  deckEl.addEventListener(
    "click",
    (event) => {
      if (Math.abs(drag.dx) > 8 || Math.abs(drag.dy) > 8) {
        event.preventDefault();
        event.stopPropagation();
        drag.dx = 0;
        drag.dy = 0;
      }
    },
    true
  );

  // ---- touchpad (wheel) swipe -----------------------------------------------------

  // Two-finger horizontal scroll accumulates into the same visual drag. The card
  // follows the fingers' direction under natural scrolling (offset = -deltaX).
  // Vertical intent is left alone so the description keeps scrolling.
  const wheel = { x: 0, timer: 0, active: false };

  function wheelSettle() {
    wheel.timer = 0;
    const node = dragTarget();
    wheel.active = false;
    const x = wheel.x;
    wheel.x = 0;
    if (!node) return;
    const width = node.offsetWidth || 360;
    if (Math.abs(x) > width * COMMIT_AT) {
      stage.style.setProperty("--lean", "0");
      decide(x > 0 ? "interested" : "dismiss");
    } else {
      clearDrag(node);
    }
  }

  deckEl.addEventListener(
    "wheel",
    (event) => {
      const node = dragTarget();
      if (!node) return;
      const unit = event.deltaMode === 1 ? 16 : 1;
      const dx = event.deltaX * unit;
      const dy = event.deltaY * unit;
      if (!wheel.active && Math.abs(dx) <= Math.abs(dy) * 1.2) return;
      event.preventDefault();
      wheel.active = true;
      wheel.x -= dx;
      setDrag(node, wheel.x, 0, false);
      if (wheel.timer) window.clearTimeout(wheel.timer);
      wheel.timer = window.setTimeout(wheelSettle, 140);
    },
    { passive: false }
  );

  // ---- wiring ----------------------------------------------------------------------

  undoBtn.addEventListener("click", undo);
  reloadBtn.addEventListener("click", () => {
    state.cards = [];
    state.cursor = 0;
    state.seen.clear();
    state.exhausted = false;
    state.failed = false;
    els.forEach((node) => node.remove());
    els.clear();
    refill(true);
  });

  refill(true);
})();
