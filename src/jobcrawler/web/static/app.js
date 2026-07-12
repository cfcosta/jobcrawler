// Progressive enhancement only — every mutation is a real <form> that works without JS.
// A <select data-autosubmit> submits its form on change. A row-level stage change goes
// through an inline fetch (no full-page reload mid-triage); anything else (the detail
// page, the applications sort) falls back to a normal submit.
document.addEventListener("change", function (event) {
  const el = event.target;
  if (!el.matches("[data-autosubmit]") || !el.form) return;
  const form = el.form;
  if (form.classList.contains("rowstatus")) {
    event.preventDefault();
    const value = el.value;
    postForm(form.getAttribute("action"), { track_status: value }).then(() => {
      el.className = "badge-" + stageToken(value);
      Toast.show("Stage → " + labelize(value));
    });
  } else {
    form.submit();
  }
});

// The tracking CSS token / label maps, mirrored from jobcrawler.tracking so an inline
// stage change can recolor its select and name itself without a round-trip. Kept tiny;
// the server stays the source of truth (the next page load re-renders from it).
const STAGE_TOKENS = {
  new: "neutral",
  interested: "info",
  applied: "progress",
  interviewing: "warn",
  offer: "good",
  accepted: "success",
  rejected: "danger",
  withdrawn: "muted",
};
function stageToken(value) {
  return STAGE_TOKENS[value] || "muted";
}
function labelize(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

// ---------------------------------------------------------------------------
// Toasts — small, self-dismissing status cards (also the undo affordance).
// ---------------------------------------------------------------------------
const Toast = (function () {
  const host = () => document.getElementById("toasts");
  function show(message, opts) {
    const root = host();
    if (!root) return () => {};
    opts = opts || {};
    const el = document.createElement("div");
    el.className = "toast";
    const span = document.createElement("span");
    span.textContent = message;
    el.appendChild(span);
    if (opts.action) {
      const btn = document.createElement("button");
      btn.className = "toast-action";
      btn.textContent = opts.action;
      btn.addEventListener("click", function () {
        opts.onAction && opts.onAction();
        dismiss();
      });
      el.appendChild(btn);
    }
    root.appendChild(el);
    const timer = setTimeout(dismiss, opts.timeout || 5000);
    function dismiss() {
      clearTimeout(timer);
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 180);
    }
    return dismiss;
  }
  return { show };
})();

// A form-encoded POST to a mutation endpoint (endpoints answer 303; fetch follows it,
// we ignore the body). Returns the promise so callers can chain a toast/undo.
function postForm(path, data) {
  const body = new URLSearchParams(data || {});
  return fetch(path, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
    credentials: "same-origin",
  });
}

// Guarded one-way new->interested promotion (the `i` key). The server enforces the
// new-only rule and answers JSON {changed, track_status}; callers reconcile their
// optimistic UI off `changed` (false = the row wasn't `new`, so nothing was written).
function postInterested(pid) {
  return postForm(`/positions/${pid}/interested`).then((r) => r.json());
}

// ---------------------------------------------------------------------------
// Keyboard-first triage on the positions list. Inbox-zero for jobs: j/k to move,
// enter to open, o external, a/i to stage, d to dismiss (with undo), x to select.
// Every action maps to an existing endpoint; the page never needs JS to function.
// ---------------------------------------------------------------------------
(function () {
  const rows = () => Array.from(document.querySelectorAll("tr.js-row"));
  const help = document.getElementById("kbd-help");
  if (!rows().length && !help) return;
  let idx = -1;

  function current() {
    const all = rows();
    return idx >= 0 && idx < all.length ? all[idx] : null;
  }
  function focusRow(n) {
    const all = rows();
    if (!all.length) return;
    idx = Math.max(0, Math.min(n, all.length - 1));
    all.forEach((r, i) => r.classList.toggle("kbd-active", i === idx));
    const row = all[idx];
    row.scrollIntoView({ block: "nearest" });
  }
  function toggleHelp(force) {
    if (!help) return;
    const open = force === undefined ? help.hasAttribute("hidden") : force;
    if (open) help.removeAttribute("hidden");
    else help.setAttribute("hidden", "");
  }

  // Slide-over: open a position's detail as a panel over the list, keeping context.
  // ←/→ move between rows without leaving; esc/backdrop close; full page stays for
  // deep links (and is one click away via "Open full").
  const over = document.getElementById("slideover");
  const overBody = document.getElementById("slideover-body");
  function slideOpen() {
    return over && !over.hasAttribute("hidden");
  }
  function openDetail(row) {
    if (!over || !row || !row.dataset.detail) return;
    focusRow(rows().indexOf(row));
    fetch(row.dataset.detail + "?partial=1", { credentials: "same-origin" })
      .then((r) => r.text())
      .then((html) => {
        overBody.innerHTML = html;
        overBody.scrollTop = 0;
        const full = over.querySelector(".slideover-full");
        if (full) full.setAttribute("href", row.dataset.detail);
        over.removeAttribute("hidden");
        // Focus the panel so native keys (↓/↑, space, PgDn) scroll IT, not the
        // list underneath — the panel is what you are reading now.
        overBody.setAttribute("tabindex", "-1");
        overBody.focus({ preventScroll: true });
      });
  }
  function closeDetail() {
    if (over) over.setAttribute("hidden", "");
  }
  if (over) {
    over.addEventListener("click", function (event) {
      if (event.target.closest("[data-close]")) closeDetail();
      else if (event.target.closest(".slideover-prev")) focusRow(idx - 1), openDetail(current());
      else if (event.target.closest(".slideover-next")) focusRow(idx + 1), openDetail(current());
    });
  }

  function stage(row, value, label) {
    const pid = row.dataset.pid;
    postForm(`/positions/${pid}/status`, { track_status: value }).then(() => {
      // Reflect the change in the row's own stage select, if present — and in
      // the open panel's Track select (panel-mode keys act on the shown row).
      const sel = row.querySelector('select[name="track_status"]');
      if (sel) sel.value = value;
      if (slideOpen()) {
        const panelSel = overBody.querySelector('select[name="track_status"]');
        if (panelSel) panelSel.value = value;
      }
      Toast.show(`Marked ${label}`);
    });
  }
  // The `i` key is a one-way new->interested promotion, not a free stage set — so it
  // routes through the guarded endpoint (not `stage`) and only reflects the change if
  // the server actually made it. Pressing `i` on a rejected/withdrawn/applied row is a
  // no-op that says so, instead of silently reviving it into the funnel.
  function promoteInterested(row) {
    postInterested(row.dataset.pid).then((res) => {
      if (!res.changed) {
        Toast.show("Only new positions can move to interested");
        return;
      }
      const sel = row.querySelector('select[name="track_status"]');
      if (sel) sel.value = res.track_status;
      if (slideOpen()) {
        const panelSel = overBody.querySelector('select[name="track_status"]');
        if (panelSel) panelSel.value = res.track_status;
      }
      Toast.show("Marked interested");
    });
  }
  function dismissRow(row) {
    const pid = row.dataset.pid;
    postForm(`/positions/${pid}/dismiss`, { dismissed: "1" }).then(() => {
      row.classList.add("dimmed");
      Toast.show("Dismissed", {
        action: "Undo",
        onAction: () =>
          postForm(`/positions/${pid}/dismiss`, { dismissed: "0" }).then(() =>
            row.classList.remove("dimmed"),
          ),
      });
    });
  }

  document.addEventListener("keydown", function (event) {
    // Never hijack typing in a field, and let modifier combos through to the browser.
    const t = event.target;
    const typing =
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "Escape") {
      // Topmost layer first: help card, then a focused panel field, then the panel.
      if (help && !help.hasAttribute("hidden")) {
        toggleHelp(false);
        return;
      }
      if (typing && slideOpen() && over.contains(t)) {
        t.blur();
        overBody.focus({ preventScroll: true });
        return;
      }
      if (slideOpen()) closeDetail();
      return;
    }
    // Panel mode: with the slide-over open the keys act on the SHOWN position.
    // j/k (and ←/→) page through positions in place; o/a/i/d target what you
    // are looking at; n jumps to notes; f opens the full page. Nothing falls
    // through to the list bindings while the panel is up.
    if (slideOpen()) {
      if (typing) return;
      switch (event.key) {
        case "ArrowLeft":
        case "k":
          event.preventDefault();
          focusRow(idx <= 0 ? 0 : idx - 1);
          openDetail(current());
          break;
        case "ArrowRight":
        case "j":
          event.preventDefault();
          focusRow(idx + 1);
          openDetail(current());
          break;
        case "o": {
          const row = current();
          if (row && row.dataset.ext) window.open(row.dataset.ext, "_blank", "noopener");
          break;
        }
        case "f": {
          const row = current();
          if (row && row.dataset.detail) window.location.assign(row.dataset.detail);
          break;
        }
        case "a": {
          const row = current();
          if (row) stage(row, "applied", "applied");
          break;
        }
        case "i": {
          const row = current();
          if (row) promoteInterested(row);
          break;
        }
        case "d": {
          const row = current();
          if (row) dismissRow(row);
          break;
        }
        case "n": {
          event.preventDefault();
          const notes = overBody.querySelector('textarea[name="notes"]');
          if (notes) {
            notes.focus();
            notes.setSelectionRange(notes.value.length, notes.value.length);
          }
          break;
        }
        case "?":
          event.preventDefault();
          toggleHelp();
          break;
      }
      return;
    }
    if (typing) return;

    switch (event.key) {
      case "j":
        event.preventDefault();
        focusRow(idx + 1);
        break;
      case "k":
        event.preventDefault();
        focusRow(idx <= 0 ? 0 : idx - 1);
        break;
      case "Enter": {
        const row = current();
        if (row) openDetail(row);
        break;
      }
      case "o": {
        const row = current();
        if (row && row.dataset.ext) window.open(row.dataset.ext, "_blank", "noopener");
        break;
      }
      case "a": {
        const row = current();
        if (row) stage(row, "applied", "applied");
        break;
      }
      case "i": {
        const row = current();
        if (row) promoteInterested(row);
        break;
      }
      case "d": {
        const row = current();
        if (row) dismissRow(row);
        break;
      }
      case "x": {
        const row = current();
        const box = row && row.querySelector("input.bulk-check");
        if (box) {
          box.checked = !box.checked;
          box.dispatchEvent(new Event("change", { bubbles: true }));
        }
        break;
      }
      case "/":
        event.preventDefault();
        {
          const box = document.querySelector('.search input[type="search"]');
          if (box) box.focus();
        }
        break;
      case "?":
        event.preventDefault();
        toggleHelp();
        break;
    }
  });

  // A plain click on the title link opens the slide-over instead of navigating
  // (cmd/ctrl/middle-click still open the full page in a new tab). Clicking the row
  // body elsewhere just highlights it, so mouse and keyboard share one "current".
  document.addEventListener("click", function (event) {
    const row = event.target.closest && event.target.closest("tr.js-row");
    if (!row) return;
    const titleLink = event.target.closest("a.pos-link");
    if (titleLink && over && !event.metaKey && !event.ctrlKey && event.button === 0) {
      event.preventDefault();
      openDetail(row);
      return;
    }
    // §17 role groups: when this row bundles several locations, the ↗ jump would
    // silently pick one — first click opens the location picker instead; a second
    // click (picker already open) proceeds to the shown variant. Modified clicks
    // and no-JS keep the direct link.
    const extLink = event.target.closest("a.row-ext");
    if (extLink && !event.metaKey && !event.ctrlKey && event.button === 0) {
      const menu = row.querySelector("details.locmenu");
      if (menu && !menu.open) {
        event.preventDefault();
        menu.open = true;
        return;
      }
    }
    // `details` covers the location-variant picker (§17 role groups): toggling or
    // choosing from the popup must not also change the focused row / open the panel.
    if (event.target.closest("a, button, select, input, form, details")) return;
    focusRow(rows().indexOf(row));
  });

  // Only one location picker open at a time, and any outside click closes it —
  // native <details> keeps the no-JS fallback (popups just stack open).
  document.addEventListener("click", function (event) {
    const inside = event.target.closest && event.target.closest("details.locmenu");
    document.querySelectorAll("details.locmenu[open]").forEach(function (menu) {
      if (menu !== inside) menu.removeAttribute("open");
    });
  });
})();

// ---------------------------------------------------------------------------
// Bulk selection on the positions list: reflect the checked count into the sticky
// action bar (hidden until ≥1 row is selected), wire the header "select all" box, and
// let Enter in the note field trigger "Append note". Pure enhancement — without JS the
// bar is always visible (via a <noscript> style) and each button submits a real form.
// ---------------------------------------------------------------------------
(function () {
  const bar = document.getElementById("bulk");
  if (!bar) return;
  const all = document.getElementById("bulk-all");
  const countEl = bar.querySelector("[data-bulk-count]");
  const boxes = () => Array.from(document.querySelectorAll("input.bulk-check"));

  function sync() {
    const all_ = boxes();
    const checked = all_.filter((b) => b.checked);
    if (countEl) countEl.textContent = String(checked.length);
    bar.classList.toggle("is-empty", checked.length === 0);
    all_.forEach((b) => b.closest("tr").classList.toggle("selected", b.checked));
    if (all) {
      all.checked = checked.length > 0 && checked.length === all_.length;
      all.indeterminate = checked.length > 0 && checked.length < all_.length;
    }
  }

  document.addEventListener("change", function (event) {
    const el = event.target;
    if (el === all) {
      boxes().forEach((b) => (b.checked = all.checked));
      sync();
    } else if (el.classList && el.classList.contains("bulk-check")) {
      sync();
    }
  });

  const note = bar.querySelector('input[name="note"]');
  if (note) {
    note.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        const btn = bar.querySelector('button[value="note"]');
        if (btn) btn.click();
      }
    });
  }

  // Inline bulk apply: POST the selection via fetch and update the affected rows in
  // place instead of a full-page reload. The submitter names the action; without JS the
  // same form posts normally (the server 303s back to the list).
  bar.addEventListener("submit", function (event) {
    const action = (event.submitter && event.submitter.value) || "";
    if (!action) return;
    event.preventDefault();
    const selected = boxes().filter((b) => b.checked);
    if (!selected.length) return;
    const data = new URLSearchParams();
    data.append("action", action);
    selected.forEach((b) => data.append("pids", b.value));
    if (action === "status") {
      const sel = bar.querySelector('select[name="track_status"]');
      if (sel) data.append("track_status", sel.value);
    } else if (action === "note") {
      const n = bar.querySelector('input[name="note"]');
      if (n) data.append("note", n.value);
    }
    postForm("/positions/bulk", data).then(() => {
      const rows = selected.map((b) => b.closest("tr"));
      if (action === "dismiss") rows.forEach((r) => r && r.classList.add("dimmed"));
      if (action === "restore") rows.forEach((r) => r && r.classList.remove("dimmed"));
      if (action === "status") {
        const value = bar.querySelector('select[name="track_status"]').value;
        rows.forEach((r) => {
          const rs = r && r.querySelector('.rowstatus select[name="track_status"]');
          if (rs) {
            rs.value = value;
            rs.className = "badge-" + stageToken(value);
          }
        });
      }
      selected.forEach((b) => (b.checked = false));
      if (note) note.value = "";
      sync();
      Toast.show(bulkMessage(action, selected.length));
    });
  });

  function bulkMessage(action, n) {
    const verb = {
      dismiss: "Dismissed",
      restore: "Restored",
      status: "Staged",
      note: "Noted",
    }[action];
    return `${verb} ${n} position${n === 1 ? "" : "s"}`;
  }

  sync();
})();

// ---------------------------------------------------------------------------
// Standalone detail page (/positions/{pid}): the list IIFE bails when there are no
// rows, so the full page would otherwise have no keys. Bind just the one-way `i`
// promotion here, against the single `.detail[data-pid]`. Guard: if a list is present
// (tr.js-row exists) it owns the keyboard and this stays inert — so on the list page,
// where the slide-over also injects a `.detail[data-pid]`, the two never both fire.
// ---------------------------------------------------------------------------
(function () {
  if (document.querySelector("tr.js-row")) return; // list mode owns the keys
  const detail = document.querySelector(".detail[data-pid]");
  if (!detail) return;
  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key !== "i") return;
    const t = event.target;
    if (
      t &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")
    )
      return; // don't hijack `i` while typing in the notes/stage fields
    postInterested(detail.dataset.pid).then((res) => {
      if (!res.changed) {
        Toast.show("Only new positions can move to interested");
        return;
      }
      const sel = detail.querySelector('select[name="track_status"]');
      if (sel) sel.value = res.track_status;
      Toast.show("Marked interested");
    });
  });
})();
