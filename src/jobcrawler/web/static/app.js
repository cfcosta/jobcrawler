// Progressive enhancement only — every mutation is a real <form> that works without JS.
// A <select data-autosubmit> submits its form on change, so changing a position's stage
// (in the list or on the detail page) is one click instead of change-then-submit.
document.addEventListener("change", function (event) {
  const el = event.target;
  if (el.matches("[data-autosubmit]") && el.form) {
    el.form.submit();
  }
});
