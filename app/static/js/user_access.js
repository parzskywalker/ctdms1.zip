// User screen (Administration > Users): scope pickers, individual-permission cells and the live "Effective access"
// preview. Everything here only helps the person filling in the form - the server re-checks all of it on save.
(function () {
  'use strict';
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const form = $('#user-form');
  if (!form) return;

  // ---------------------------------------------------------------- scope pickers (areas, customers)
  function initPicker(box) {
    const kind = box.dataset.kind, name = box.dataset.name;
    const all = $('[data-picker-all]', box), body = $('.picker-body', box);
    const q = $('.picker-q', box), list = $('.picker-results', box), chosen = $('.picker-chosen', box);
    let timer = null, hits = [], cur = -1, seq = 0, busy = false, enterWaits = false;

    const has = (id) => $$('input[type=hidden]', chosen).some((i) => i.value === String(id));
    function add(hit) {
      if (!has(hit.id)) {
        const chip = document.createElement('span');
        chip.className = 'chip';
        const inp = document.createElement('input');
        inp.type = 'hidden'; inp.name = name; inp.value = hit.id;
        const btn = document.createElement('button');
        btn.type = 'button'; btn.textContent = '×'; btn.setAttribute('aria-label', 'Remove ' + hit.label);
        chip.append(inp, document.createTextNode(hit.label + ' '), btn);
        chosen.appendChild(chip);
        changed();
      }
      q.value = ''; close(); q.focus();
    }
    function close() { list.hidden = true; list.textContent = ''; hits = []; cur = -1; enterWaits = false; }
    function show(rows) {
      hits = rows.filter((r) => !has(r.id)); cur = hits.length ? 0 : -1;
      list.textContent = '';
      hits.forEach((h, i) => {
        const li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.textContent = h.label;
        if (i === cur) li.className = 'hi';
        li.addEventListener('mousedown', (e) => { e.preventDefault(); add(h); });
        list.appendChild(li);
      });
      if (!hits.length) { const li = document.createElement('li'); li.className = 'none'; li.textContent = 'No match'; list.appendChild(li); }
      list.hidden = false;
    }
    function move(d) {
      if (!hits.length) return;
      cur = (cur + d + hits.length) % hits.length;
      $$('li', list).forEach((li, i) => li.classList.toggle('hi', i === cur));
    }
    function search() {
      const mine = ++seq;
      busy = true;
      fetch('/admin/picker?kind=' + encodeURIComponent(kind) + '&q=' + encodeURIComponent(q.value.trim()), {credentials: 'same-origin'})
        .then((r) => (r.ok ? r.json() : []))
        .then((rows) => {
          if (mine !== seq) return;
          busy = false;
          if (document.activeElement === q || enterWaits) show(rows);
          if (enterWaits) { enterWaits = false; if (hits.length) add(hits[0]); }
        })
        .catch(() => { if (mine === seq) { busy = false; enterWaits = false; } });
    }
    // Whatever was listed - or is still on its way - belongs to the OLD text: drop it at once (seq++ discards late
    // answers), so a fast Enter can never pick a stale entry.
    q.addEventListener('input', () => { clearTimeout(timer); seq++; close(); busy = true; timer = setTimeout(search, 150); });
    q.addEventListener('focus', () => { if (!q.value) search(); });
    q.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== q) close(); }, 120));
    q.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) search(); else move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') {
        e.preventDefault();  // never submits the form, never jumps to the next field from here
        if (busy && q.value.trim()) enterWaits = true;  // the search for this text is still running: take its first hit
        else if (!list.hidden && cur >= 0) add(hits[cur]);
        else if (!q.value.trim() && window.CTDMSKeys && window.CTDMSKeys.next) window.CTDMSKeys.next(q);
      } else if (e.key === 'Escape' && !list.hidden) { e.preventDefault(); e.stopPropagation(); close(); }
    });
    chosen.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (b) { b.closest('.chip').remove(); changed(); q.focus(); }
    });
    all.addEventListener('change', () => { body.hidden = all.checked; changed(); });
  }
  $$('[data-picker]').forEach(initPicker);

  // customer types: the list appears when "All customer types" is off
  const allTypes = $('#all-ctypes'), typeList = $('#ctype-list');
  if (allTypes) allTypes.addEventListener('change', () => { typeList.hidden = allTypes.checked; changed(); });

  // ---------------------------------------------------------------- individual permissions follow the role
  const rolePerms = JSON.parse($('#role-perms').textContent || '{}');
  const roleSel = $('#role-select');
  function roleHas(p) {
    const l = rolePerms[roleSel.value] || [];
    return l.indexOf('*') >= 0 || l.indexOf(p) >= 0;
  }
  function syncPerms() {
    $$('select[data-perm]').forEach((s) => {
      const p = s.dataset.perm, has = roleHas(p);
      const cell = $('[data-role-has="' + p + '"]');
      if (cell) cell.textContent = has ? 'Yes' : 'No';
      $$('option', s).forEach((o) => {
        o.disabled = (o.value === 'GRANT' && has) || (o.value === 'DENY' && !has);  // would change nothing
      });
      if (s.selectedOptions[0] && s.selectedOptions[0].disabled) s.value = '';
    });
  }
  roleSel.addEventListener('change', () => { syncPerms(); changed(); });
  syncPerms();

  // ---------------------------------------------------------------- live preview (nothing is saved)
  const warn = $('#eff-warn'), chg = $('#eff-changes'), menus = $('#eff-menus');
  let ptimer = null, pseq = 0;
  function line(parent, text, cls) {
    const d = document.createElement('div');
    if (cls) d.className = cls;
    d.textContent = text;
    parent.appendChild(d);
  }
  function scopeWarnings() {
    const w = [];
    const off = (n) => { const c = form.elements[n]; return c && !c.checked; };
    const none = (n) => !$$('input[name="' + n + '"]', form).some((i) => i.type !== 'checkbox' || i.checked);
    if (off('all_companies') && none('companies')) w.push('No company selected - this person will see no invoices.');
    if (off('all_cfas') && none('cfas')) w.push('No depot selected - this person will see no depot data.');
    if (off('all_areas') && none('areas')) w.push('No area selected - this person will see no invoices.');
    if (off('all_customer_types') && off('all_customers') && none('customer_types') && none('customers')) w.push('No customer type or customer selected - this person will see no invoices.');
    return w;
  }
  function render(d) {
    chg.textContent = menus.textContent = warn.textContent = '';
    scopeWarnings().forEach((t) => line(warn, '⚠ ' + t));
    if (d.error) { line(chg, d.error); return; }
    line(chg, d.permissions.length + ' permissions in effect (role gives ' + d.from_role.length + ').');
    d.granted.forEach((p) => line(chg, '+ ' + p, 'eff-add'));
    d.denied.forEach((p) => line(chg, '− ' + p, 'eff-del'));
    if (!d.can_manage_access) line(chg, 'Cannot manage users or roles.', 'muted');
    const h = document.createElement('div');
    h.className = 'eff-h'; h.textContent = 'Menu this person gets';
    menus.appendChild(h);
    d.menus.forEach((m) => line(menus, m.group + ': ' + m.items.join(', ')));
    if (!d.menus.length) line(menus, 'No menu - the person can sign in but sees nothing.', 'muted');
  }
  function preview() {
    const mine = ++pseq;
    const fd = new URLSearchParams(new FormData(form));
    fd.delete('password');  // the preview never needs it
    fetch('/admin/users/preview', {method: 'POST', body: fd, credentials: 'same-origin',
                                   headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
      .then((r) => r.json().catch(() => ({error: 'Preview unavailable'})))
      .then((d) => { if (mine === pseq) render(d); })
      .catch(() => {});
  }
  function changed() { clearTimeout(ptimer); ptimer = setTimeout(preview, 200); }
  form.addEventListener('change', changed);
  preview();
})();
