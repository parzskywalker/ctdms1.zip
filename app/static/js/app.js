// CTDMS UI helpers - vanilla JS, no dependencies.
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  // Mobile / tablet navigation
  const mb = $('.menu-btn');
  if (mb) mb.addEventListener('click', () => document.body.classList.toggle('nav-open'));
  document.addEventListener('click', (e) => {
    if (document.body.classList.contains('nav-open') && !e.target.closest('.side') && !e.target.closest('.menu-btn')) {
      document.body.classList.remove('nav-open');
    }
  });

  // "/" focuses the quick invoice search
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      const q = $('#quick-q') || $('#pending-q');
      if (q) { e.preventDefault(); q.focus(); q.select(); }
    }
  });

  // Select-all checkbox for bulk tables
  $$('[data-check-all]').forEach((master) => {
    const name = master.getAttribute('data-check-all');
    master.addEventListener('change', () => {
      $$(`input[type=checkbox][name="${name}"]`).forEach((c) => { if (!c.disabled) { c.checked = master.checked; c.dispatchEvent(new Event('change')); } });
    });
  });
  const bulkCount = $('#bulk-count');
  function updateBulk() {
    if (!bulkCount) return;
    const n = $$('input[type=checkbox][name="ids"]:checked').length;
    bulkCount.textContent = n;
    $$('[data-needs-selection]').forEach((b) => { b.disabled = n === 0; });
    $$('input[type=checkbox][name="ids"]').forEach((c) => c.closest('tr') && c.closest('tr').classList.toggle('sel', c.checked));
  }
  $$('input[type=checkbox][name="ids"]').forEach((c) => c.addEventListener('change', updateBulk));
  updateBulk();

  // Forms that open output in a new tab (slip print) refresh this page afterwards
  $$('form[data-refresh-after]').forEach((f) => {
    f.addEventListener('submit', () => setTimeout(() => window.location.reload(), 1500));
  });

  // Double-submit protection
  $$('form').forEach((f) => {
    if (f.hasAttribute('data-allow-resubmit') || f.target === '_blank') return;
    f.addEventListener('submit', () => {
      $$('button[type=submit], button:not([type])', f).forEach((b) => {
        setTimeout(() => { b.disabled = true; }, 0);
        setTimeout(() => { b.disabled = false; }, 8000);
      });
    });
  });

  // Confirmation that needs a typed reason: <button data-reason-for="#field">
  $$('[data-require-reason]').forEach((b) => {
    b.addEventListener('click', (e) => {
      const fld = $(b.getAttribute('data-require-reason'));
      if (fld && fld.value.trim().length < 5) {
        e.preventDefault();
        fld.focus();
        fld.style.outline = '2px solid #e07a70';
        const hint = fld.parentElement.querySelector('.field-hint');
        if (hint) hint.textContent = 'Please enter a reason (min 5 characters).';
      }
    });
  });

  // Click-to-fill demo credentials on the login page
  $$('[data-fill-user]').forEach((el) => el.addEventListener('click', () => {
    const u = $('#username'), p = $('#password');
    if (u && p) { u.value = el.getAttribute('data-fill-user'); p.value = el.getAttribute('data-fill-pass'); p.focus(); }
  }));

  // Date range presets in filter bars
  $$('[data-preset]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault();
    const form = a.closest('form');
    const [from, to] = a.getAttribute('data-preset').split('|');
    form.querySelector('[name=date_from]').value = from;
    form.querySelector('[name=date_to]').value = to;
    form.submit();
  }));

  // Auto-uppercase fields
  $$('input[data-upper]').forEach((i) => i.addEventListener('input', () => {
    const p = i.selectionStart; i.value = i.value.toUpperCase(); i.setSelectionRange(p, p);
  }));
})();
