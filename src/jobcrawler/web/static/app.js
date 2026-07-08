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

  function stage(row, value, label) {
    const pid = row.dataset.pid;
    postForm(`/positions/${pid}/status`, { track_status: value }).then(() => {
      // Reflect the change in the row's own stage select, if present.
      const sel = row.querySelector('select[name="track_status"]');
      if (sel) sel.value = value;
      Toast.show(`Marked ${label}`);
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
      toggleHelp(false);
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
        if (row && row.dataset.detail) window.location.href = row.dataset.detail;
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
        if (row) stage(row, "interested", "interested");
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

  // Clicking a row body (not a link/control) highlights it, so mouse and keyboard
  // share one notion of "current".
  document.addEventListener("click", function (event) {
    const row = event.target.closest && event.target.closest("tr.js-row");
    if (!row) return;
    if (event.target.closest("a, button, select, input, form")) return;
    focusRow(rows().indexOf(row));
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
