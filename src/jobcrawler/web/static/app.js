// Progressive enhancement only — every mutation is a real <form> that works without JS.
// A <select data-autosubmit> submits its form on change, so changing a position's stage
// (in the list or on the detail page) is one click instead of change-then-submit.
document.addEventListener("change", function (event) {
  const el = event.target;
  if (el.matches("[data-autosubmit]") && el.form) {
    el.form.submit();
  }
});

// Bulk selection on the positions list: reflect the checked count into the sticky
// action bar (hidden until ≥1 row is selected), wire the header "select all" box, and
// let Enter in the note field trigger "Append note". Pure enhancement — without JS the
// bar is always visible (via a <noscript> style) and each button submits a real form.
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

  sync();
})();
