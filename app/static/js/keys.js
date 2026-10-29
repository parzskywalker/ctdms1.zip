// CTDMS keyboard-first layer (v1.6). Every daily screen can be worked from the keyboard; the mouse is optional.
//
// One key = one meaning on every screen:
//   F1 help · F2 new · F3 go to screen · F4 search · F6 save · F7 edit · F8 make the gatepass / print
//   F10 reports · Esc close / back · Enter next field · arrows move in lists · Space ticks
// F5 (refresh), F11 (full screen) and F12 are left to the browser.
//
// Screens say which button a key presses with data-key="F6" (a hint "F6" is added to the button).
// Without a data-key: F2 = the "New…/Add…" button at the top, F4 = the screen's search box,
// F6 = the main button of the form the cursor is in, F7 = an "Edit" button, Esc = the breadcrumb (one level up).
(function () {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const KB = window.CTDMSKeys = window.CTDMSKeys || {};
  const touchOnly = !!(window.matchMedia && matchMedia('(hover: none) and (pointer: coarse)').matches);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  const KEYS = [
    ['F1', 'Help', 'Show this list of keys'],
    ['F2', 'New', 'Add a new record on this screen (dispatch desk: new gatepass)'],
    ['F3', 'Go to', 'Jump to any screen: type part of its name, Enter (also Ctrl+K)'],
    ['F4', 'Search', 'Put the cursor in the search box of this screen'],
    ['F6', 'Save', 'Save the form you are working in (also Ctrl+S)'],
    ['F7', 'Edit', 'Edit the record on screen'],
    ['F8', 'Gatepass / Print', 'Make the gatepass, or print it'],
    ['F10', 'Reports', 'Open the reports'],
    ['Esc', 'Back', 'Close a list, message or dialog; otherwise back one screen'],
  ];
  const LABEL = Object.fromEntries(KEYS.map(([k, l]) => [k, l]));

  // ------------------------------------------------------------------ helpers
  function visible(el) {
    if (!el || !el.isConnected || !el.getClientRects().length) return false;
    return getComputedStyle(el).visibility !== 'hidden';
  }
  function enabled(el) {
    return !(el.disabled || el.classList.contains('disabled') || el.getAttribute('aria-disabled') === 'true');
  }
  function text(el) {
    const c = el.cloneNode(true);
    $$('.kh', c).forEach((k) => k.remove());
    return (c.textContent || el.value || '').replace(/\s+/g, ' ').replace(/^[^\w(₹]+/, '').trim();
  }
  function norm(s) { return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim(); }
  function mk(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function layer() { return $('.kb-layer'); }
  const content = () => $('.content') || document.body;

  let toastT = null;
  function toast(msg, kind) {
    let t = $('#kb-toast');
    if (!t) { t = mk('div', 'kb-toast'); t.id = 'kb-toast'; t.setAttribute('role', 'status'); t.setAttribute('aria-live', 'polite'); document.body.appendChild(t); }
    t.textContent = msg;
    t.className = 'kb-toast show' + (kind ? ' ' + kind : '');
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove('show'), 4800);
  }
  function pulse(el) { el.classList.remove('kb-pulse'); void el.offsetWidth; el.classList.add('kb-pulse'); }

  function focusField(f) {
    if (!f) return;
    if (f._combo) f = f._combo.inp;
    f.focus();
    if (f.tagName === 'INPUT' && /^(text|search|number|tel|email|password|url)$/.test(f.type)) { try { f.select(); } catch (e) { /* number inputs in some browsers */ } }
  }

  // ------------------------------------------------------------------ which element a key presses
  function focusForm() {
    const a = document.activeElement;
    const f = a && (a.form || (a.closest && a.closest('form')));
    return f && f.method === 'post' ? f : null;
  }
  function explicit(k, scope) {
    const all = $$('[data-key]', scope || document).filter((el) => el.dataset.key.split(/\s+/).includes(k) && visible(el));
    if (!all.length) return null;
    const f = focusForm();
    return (f && all.find((el) => el.form === f || f.contains(el))) || all.find(enabled) || all[0];
  }
  function submitsOf(form) {
    return Array.from(form.elements).filter((b) => b.type === 'submit' && visible(b) && !b.classList.contains('btn-danger'));
  }
  function mainButton(form) {
    if (!(form instanceof HTMLFormElement)) return null;
    const t = $('[data-enter-target]', form);
    if (t && visible(t)) return t;
    const subs = submitsOf(form);
    return subs.find((b) => /\bbtn-(primary|ok)\b/.test(b.className)) || subs[0] || null;
  }
  function saveButton(form) {
    if (!form) return null;
    const subs = submitsOf(form);
    return subs.find((b) => (b.dataset.key || '').split(/\s+/).includes('F6')) || mainButton(form);
  }
  const FALLBACK = {
    F2: () => $$('.page-head .actions a.btn, .page-head .actions button.btn', content()).find((b) => /^(new|add)\b/i.test(text(b)) && visible(b)),
    F4: () => $$('input[type=search]', content()).find(visible) || $('#quick-q'),
    F7: () => $$('.page-head .actions a.btn', content()).find((b) => /^edit\b/i.test(text(b)) && visible(b)),
    F10: () => $('#kb-reports'),
  };
  function resolve(k) {
    if (k === 'F6') {
      const f = focusForm();
      return (f && (explicit('F6', f) || saveButton(f))) || explicit('F6');
    }
    return explicit(k) || (FALLBACK[k] ? FALLBACK[k]() : null);
  }
  function whyNot(el) {
    const w = el.dataset.why && $(el.dataset.why);
    if (w && w.textContent.trim()) return w.textContent.trim();
    if (el.title) return el.title;
    for (const box of [el.closest('form'), el.closest('.card')]) {
      const bl = box && $('.blockers', box);
      if (bl && bl.innerText.trim()) return 'Not yet: ' + bl.innerText.trim().replace(/\s*\n\s*/g, '; ');
    }
    return '"' + text(el) + '" is not available yet on this screen.';
  }
  function activate(el, k) {
    if (!el) { toast(k + ' (' + (LABEL[k] || '') + ') is not used on this screen. Press F1 for the keys of this screen.'); return; }
    if (!enabled(el)) { toast(whyNot(el), 'warn'); return; }
    if (el.matches('input, select, textarea')) { focusField(el); return; }
    pulse(el);
    el.click();
  }
  function run(k) {
    const open = layer();
    if (open) { if (k === 'F1' && open.id === 'kb-help') closeLayer(open); return; }
    if (k === 'F1') { help(); return; }
    if (k === 'F3') { goTo(); return; }
    activate(resolve(k), k);
  }

  // F-keys are caught before the page (and the browser) sees them; Ctrl+S = F6, Ctrl+K = F3.
  window.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    let k = e.key;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
      const c = (k || '').toLowerCase();
      if (c === 's') k = 'F6'; else if (c === 'k') k = 'F3'; else return;
    } else if (e.ctrlKey || e.altKey || e.metaKey) return;
    if (!/^F([1-46-9]|10)$/.test(k)) return;
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat) run(k);
  }, true);

  // Alt+D = depot switcher (only exists for users assigned to several depots); Enter on it switches
  document.addEventListener('keydown', (e) => {
    const d = $('#depot-switch');
    if (!d) return;
    if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key || '').toLowerCase() === 'd') { e.preventDefault(); d.focus(); return; }
    if (e.key === 'Enter' && e.target === d && !e.defaultPrevented) { e.preventDefault(); d.form.submit(); }
  });

  // ------------------------------------------------------------------ Enter = next field (ERP style)
  function chain(scope) {
    const seen = new Set();
    return $$('input, select, textarea', scope).filter((f) => {
      if (f.type === 'hidden' || f.disabled || f.tabIndex < 0 || !visible(f)) return false;
      if (/^(submit|button|reset|image)$/.test(f.type)) return false;
      if (f.readOnly && !/^(checkbox|radio)$/.test(f.type)) return false;
      if (f.closest('[data-enter-skip], .kb-layer')) return false;
      if (f.type === 'radio') { if (seen.has(f.name)) return false; seen.add(f.name); }
      return true;
    }).map((f) => (f.type === 'radio' ? radioRep(f) : f));
  }
  function radioRep(r) {
    const g = $$('input[type=radio]', r.form || document).filter((x) => x.name === r.name && visible(x) && !x.disabled);
    return g.find((x) => x.checked) || g[0] || r;
  }
  function move(el, dir) {
    const scope = el.form || el.closest('[data-enter-next]') || content();
    const list = chain(scope);
    const i = list.findIndex((f) => f === el || (el.type === 'radio' && f.type === 'radio' && f.name === el.name));
    const nxt = list[i + dir];
    if (nxt) { focusField(nxt); return; }
    if (dir < 0) return;
    const btn = mainButton(scope);
    if (!btn) return;
    if (!enabled(btn)) { toast(whyNot(btn), 'warn'); return; }
    if (scope.dataset && scope.dataset.enterLast === 'submit') { btn.click(); return; }
    btn.focus();
    toast('Press Enter to "' + text(btn) + '" - or Shift+Tab to go back.');
  }
  function onEnter(e) {
    if (e.defaultPrevented || e.altKey || e.metaKey) return;
    const el = e.target;
    if (!(el instanceof HTMLElement) || el.closest('.kb-layer')) return;
    if (el.tagName === 'TEXTAREA') { if (e.ctrlKey) { e.preventDefault(); move(el, 1); } return; }
    if (e.ctrlKey || (el.tagName !== 'INPUT' && el.tagName !== 'SELECT')) return;
    if (/^(submit|button|reset|image|file)$/.test(el.type)) return;
    const form = el.form;
    if (form && form.method !== 'post' && !form.hasAttribute('data-enter-next')) return;  // search / filter boxes: Enter applies
    if (!form && !el.closest('[data-enter-next]')) return;
    e.preventDefault();
    move(el, e.shiftKey ? -1 : 1);
  }

  // ------------------------------------------------------------------ Esc = close, otherwise back one screen
  const snaps = new Map();
  function snap(f) {
    const out = [];
    new FormData(f).forEach((v, k) => { if (k !== 'csrf_token') out.push(k + '=' + (typeof v === 'string' ? v : v.name)); });
    return out.join('&');
  }
  function takeSnaps() { snaps.clear(); $$('form', content()).forEach((f) => { if (f.method === 'post' && !f.hasAttribute('data-no-dirty')) snaps.set(f, snap(f)); }); }
  function dirty() { for (const [f, s] of snaps) { if (f.isConnected && snap(f) !== s) return true; } return false; }
  function back() {
    const ex = explicit('Esc');
    let href = cameFrom();
    if (href) { /* back to the list it was opened from */ }
    else if (ex && ex.tagName !== 'A') { ex.click(); return; }
    else if (ex) href = ex.href;
    else {
      const cr = $$('.crumbs a');
      if (cr.length) href = cr[cr.length - 1].href;
      else if ($('.side') && location.pathname !== homePath()) href = homePath();
    }
    if (!href) return;
    if (dirty()) {
      ask('Leave this screen? What you typed here has not been saved.', {danger: true, yes: 'Leave without saving', no: 'Stay here'})
        .then((ok) => { if (ok) location.href = href; });
    } else location.href = href;
  }
  function homePath() {  // the role's own first screen (Dashboard, Approvals ...), else the first screen of the menu
    const h = document.body.dataset.home;
    if (h && $(`.side a[href="${h}"]`)) return h;
    if ($('.side a[href="/"]')) return '/';
    const first = $('.side a[href]');
    return first ? first.getAttribute('href') : '/';
  }
  function onEsc(e) {
    if (e.defaultPrevented) return;
    const open = layer();
    if (open) { e.preventDefault(); closeLayer(open); return; }
    const a = document.activeElement;
    const det = a && a.closest && a.closest('details[open]');
    if (det) { e.preventDefault(); det.open = false; const s = $('summary', det); if (s) s.focus(); return; }
    if (a && a.matches && a.matches('input[type=search]') && a.value) {
      e.preventDefault(); a.value = ''; a.dispatchEvent(new Event('input', {bubbles: true})); return;
    }
    if (touchOnly) return;
    e.preventDefault();
    back();
  }
  document.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    if (e.key === 'Enter') onEnter(e);
    else if (e.key === 'Escape') onEsc(e);
  });

  // ------------------------------------------------------------------ Yes / No dialog (replaces the browser's confirm)
  function closeLayer(l) {
    const prev = l._prev;
    l.remove();
    if (prev && prev.isConnected && prev.focus) prev.focus();
    if (l._done) l._done(false);
  }
  function ask(message, opt) {
    opt = opt || {};
    return new Promise((resolve) => {
      const l = mk('div', 'kb-layer kb-modal-wrap');
      l.id = 'kb-ask';
      l._prev = document.activeElement;
      l.innerHTML = '<div class="kb-modal" role="alertdialog" aria-modal="true" aria-labelledby="kb-ask-t">'
        + '<div class="kb-ask-msg" id="kb-ask-t"></div><div class="kb-ask-btns">'
        + '<button type="button" class="btn ' + (opt.danger ? 'btn-danger' : 'btn-primary') + '" data-yes></button>'
        + '<button type="button" class="btn" data-no></button></div>'
        + '<div class="small muted kb-ask-hint">Y = yes · N or Esc = no · ← → or Tab to switch · Enter presses the highlighted button</div></div>';
      $('.kb-ask-msg', l).textContent = message;
      const yes = $('[data-yes]', l), no = $('[data-no]', l);
      yes.innerHTML = esc(opt.yes || 'Yes') + ' <kbd class="kh">Y</kbd>';
      no.innerHTML = esc(opt.no || 'No') + ' <kbd class="kh">N</kbd>';
      let settled = false;
      const done = (v) => {
        if (settled) return;
        settled = true; l._done = null; l.remove(); resolve(v);
        if (!v && l._prev && l._prev.isConnected && l._prev.focus) l._prev.focus();
      };
      l._done = done;
      yes.addEventListener('click', () => done(true));
      no.addEventListener('click', () => done(false));
      l.addEventListener('keydown', (e) => {
        const k = (e.key || '').toLowerCase();
        if (e.repeat && (k === 'enter' || k === ' ')) { e.preventDefault(); return; }  // a held-down key never answers
        if (k === 'escape' || k === 'n') { e.preventDefault(); e.stopPropagation(); done(false); }
        else if (k === 'y') { e.preventDefault(); e.stopPropagation(); done(true); }
        else if (k === 'arrowleft' || k === 'arrowright' || k === 'tab') { e.preventDefault(); (document.activeElement === yes ? no : yes).focus(); }
      });
      document.body.appendChild(l);
      (opt.danger ? no : yes).focus();
    });
  }

  // ------------------------------------------------------------------ searchable choice boxes (type code or name)
  const SEL_VALUE = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  const SEL_INDEX = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  const MASTER_FIELD = /(^|_)(company|cfa|depot|customer|ctype|customer_type|party_type|location|transporter|outside_auto|auto|erp|erp_system|source|target|city|marking)(_id)?$/i;
  let comboN = 0;
  let openCombo = null;

  function optItems(sel) {
    return Array.from(sel.options).filter((o) => !o.hidden && !(o.disabled && !o.selected)).map((o, i) => {
      const txt = o.text.replace(/\s+/g, ' ').trim();
      let code = o.dataset.code || '', name = txt;
      const m = txt.match(/^(\S{1,24}) · (.+)$/);
      if (m && (!code || code === m[1])) { code = m[1]; name = m[2]; }
      return {value: o.value, text: txt, name, code, hay: norm(txt + ' ' + code + ' ' + (o.dataset.k || '')), i};
    });
  }
  function score(it, q) {
    const words = q.split(' ');
    if (!words.every((w) => it.hay.includes(w))) return -1;
    const code = norm(it.code), name = norm(it.name);
    const codeToks = code.split(/[^a-z0-9]+/), toks = it.hay.split(/[^a-z0-9]+/);
    if (code === q || codeToks.includes(q)) return 100;
    if (toks.includes(q)) return 90;
    if (code.startsWith(q)) return 80;
    if (name.startsWith(q)) return 70;
    if (toks.some((t) => t.startsWith(words[0]))) return 50;
    return 10;
  }
  function rank(items, q) {
    q = norm(q);
    if (!q) return items;
    return items.map((it) => [score(it, q), it]).filter(([s]) => s >= 0)
      .sort((a, b) => b[0] - a[0] || a[1].i - b[1].i).map(([, it]) => it);
  }
  function wants(sel) {
    if (sel._combo || sel.multiple || sel.size > 1 || 'native' in sel.dataset) return false;
    if ('combo' in sel.dataset) return true;
    return MASTER_FIELD.test(sel.name || '') || sel.options.length > 7;
  }
  function fieldName(el) {
    const l = (el.labels && el.labels[0]) || (el._combo && el._combo.inp.labels && el._combo.inp.labels[0]);
    return l ? l.textContent.replace(/\*/g, '').trim().toLowerCase() : 'a value';
  }

  function enhance(sel) {
    const wrap = mk('span', 'combo');
    const st = sel.getAttribute('style');
    if (st) { wrap.setAttribute('style', st); sel.removeAttribute('style'); }
    sel.parentNode.insertBefore(wrap, sel);
    const inp = mk('input', 'combo-input');
    inp.type = 'text'; inp.autocomplete = 'off'; inp.spellcheck = false;
    inp.id = (sel.id || ('cb' + (++comboN))) + '-q';
    inp.setAttribute('role', 'combobox'); inp.setAttribute('aria-autocomplete', 'list'); inp.setAttribute('aria-expanded', 'false');
    if (sel.getAttribute('aria-label')) inp.setAttribute('aria-label', sel.getAttribute('aria-label'));
    const list = mk('div', 'combo-list');
    list.id = inp.id + '-list'; list.setAttribute('role', 'listbox'); list.hidden = true;
    inp.setAttribute('aria-controls', list.id);
    wrap.append(inp, sel);
    document.body.appendChild(list);  // fixed position: never clipped by a scrolling table or card
    Array.from(sel.labels || []).forEach((l) => { l.htmlFor = inp.id; });
    sel.classList.add('combo-native'); sel.tabIndex = -1; sel.setAttribute('aria-hidden', 'true');
    if (sel.dataset.key) inp.dataset.key = sel.dataset.key;
    const c = {sel, inp, list, wrap, open: false, shown: [], hi: -1, typed: false};
    sel._combo = c;

    const blank = () => Array.from(sel.options).find((o) => o.value === '');
    function label() { const o = sel.options[SEL_INDEX.get.call(sel)]; return o && o.value !== '' ? o.text.replace(/\s+/g, ' ').trim() : ''; }
    function sync() {
      if (!c.typed) inp.value = label();
      const b = blank();
      inp.placeholder = (b && b.text.trim()) || 'Type code or name';
      inp.disabled = sel.disabled;
      wrap.classList.toggle('is-disabled', sel.disabled);
    }
    function place() {
      const r = inp.getBoundingClientRect();
      list.style.minWidth = Math.round(r.width) + 'px';
      list.style.left = Math.round(Math.max(4, Math.min(r.left, innerWidth - Math.max(list.offsetWidth, r.width) - 4))) + 'px';
      if (innerHeight - r.bottom < 220 && r.top > innerHeight - r.bottom) { list.style.top = ''; list.style.bottom = Math.round(innerHeight - r.top + 2) + 'px'; }
      else { list.style.bottom = ''; list.style.top = Math.round(r.bottom + 2) + 'px'; }
    }
    function render(q) {
      const items = optItems(sel);
      c.shown = q && q.trim() ? rank(items.filter((it) => it.value !== ''), q) : items;
      const cur = SEL_VALUE.get.call(sel);
      list.innerHTML = c.shown.length
        ? c.shown.slice(0, 300).map((it, i) => '<div class="combo-opt' + (it.value === cur ? ' cur' : '') + '" role="option" id="' + list.id + '-' + i + '" data-i="' + i + '"><span>'
            + esc(it.value === '' ? it.text : it.name) + '</span>' + (it.code && it.value !== '' ? '<span class="cc">' + esc(it.code) + '</span>' : '') + '</div>').join('')
        : '<div class="combo-empty">Nothing matches “' + esc(q) + '”. Esc puts back the old value.</div>';
      c.hi = c.shown.length ? (q && q.trim() ? 0 : Math.max(0, c.shown.findIndex((it) => it.value === cur))) : -1;
      paint();
    }
    function paint() {
      $$('.combo-opt', list).forEach((d, i) => d.classList.toggle('hi', i === c.hi));
      const h = list.children[c.hi];
      if (h && h.classList.contains('combo-opt')) { inp.setAttribute('aria-activedescendant', h.id); h.scrollIntoView({block: 'nearest'}); }
      else inp.removeAttribute('aria-activedescendant');
    }
    function openList(q) {
      if (openCombo && openCombo !== c) openCombo.close();
      render(q); list.hidden = false; c.open = true; openCombo = c; inp.setAttribute('aria-expanded', 'true'); place();
    }
    function close() { list.hidden = true; c.open = false; if (openCombo === c) openCombo = null; inp.setAttribute('aria-expanded', 'false'); }
    function choose(it) {
      c.typed = false;
      if (it && SEL_VALUE.get.call(sel) !== it.value) {
        SEL_VALUE.set.call(sel, it.value);
        sel.dispatchEvent(new Event('input', {bubbles: true}));
        sel.dispatchEvent(new Event('change', {bubbles: true}));
      }
      wrap.classList.remove('is-invalid');
      sync();
    }
    function restore() { c.typed = false; sync(); }
    c.close = close; c.place = place; c.sync = sync;

    inp.addEventListener('focus', () => setTimeout(() => { if (document.activeElement === inp && !c.typed) inp.select(); }, 0));
    inp.addEventListener('mousedown', () => {
      if (document.activeElement === inp) { if (c.open) close(); else openList(''); } else setTimeout(() => openList(''), 0);
    });
    inp.addEventListener('input', () => { c.typed = true; wrap.classList.remove('is-invalid'); openList(inp.value); });
    inp.addEventListener('keydown', (e) => {
      if (e.isComposing) return;
      const k = e.key;
      if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'PageDown' || k === 'PageUp') {
        e.preventDefault();
        if (!c.open) { openList(c.typed ? inp.value : ''); return; }
        const step = {ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8}[k];
        if (c.shown.length) { c.hi = Math.max(0, Math.min(c.shown.length - 1, c.hi + step)); paint(); }
        return;
      }
      if (k === 'Enter' && !e.ctrlKey && !e.altKey) {
        if (c.open) {
          e.preventDefault();
          if (c.hi < 0) { toast('Nothing matches “' + inp.value + '”. Type a little less, or Esc.', 'warn'); return; }
          choose(c.shown[c.hi]); close();
          if (!inp.form || inp.form.method === 'post' || inp.form.hasAttribute('data-enter-next')) move(inp, e.shiftKey ? -1 : 1);
          return;
        }
        if (c.typed) {
          const best = rank(optItems(sel).filter((it) => it.value !== ''), inp.value)[0];
          if (best) choose(best); else restore();
        }
        return;  // the shared Enter handling moves on (or applies a filter)
      }
      if (k === 'Tab') { if (c.open && c.typed && c.hi >= 0) choose(c.shown[c.hi]); close(); return; }
      if (k === 'Escape' && (c.open || c.typed)) { e.preventDefault(); e.stopPropagation(); close(); restore(); }
    });
    inp.addEventListener('blur', () => setTimeout(() => {
      if (document.activeElement === inp) return;
      close();
      if (c.typed) { if (!inp.value.trim() && blank()) choose({value: ''}); else restore(); }
    }, 150));
    list.addEventListener('mousedown', (e) => e.preventDefault());
    list.addEventListener('click', (e) => {
      const d = e.target.closest('.combo-opt');
      if (!d) return;
      choose(c.shown[Number(d.dataset.i)]); close(); inp.focus();
    });

    // page scripts that set select.value (or add options) keep the box in step
    Object.defineProperty(sel, 'value', {configurable: true, get() { return SEL_VALUE.get.call(this); }, set(v) { SEL_VALUE.set.call(this, v); sync(); }});
    Object.defineProperty(sel, 'selectedIndex', {configurable: true, get() { return SEL_INDEX.get.call(this); }, set(v) { SEL_INDEX.set.call(this, v); sync(); }});
    sel.addEventListener('change', () => { if (!c.typed) sync(); });
    new MutationObserver(sync).observe(sel, {childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected', 'hidden']});
    sel.addEventListener('focus', () => inp.focus());
    sel.addEventListener('invalid', (e) => {
      e.preventDefault();
      wrap.classList.add('is-invalid');
      if (!KB._invalidShown) {
        KB._invalidShown = true;
        setTimeout(() => { KB._invalidShown = false; }, 60);
        focusField(inp);
        toast('Please choose ' + fieldName(sel) + '.', 'warn');
      }
    });
    if (sel.form) sel.form.addEventListener('reset', () => setTimeout(restore, 0));
    sync();
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button.btn-danger, input.btn-danger');
    if (!b || b.type !== 'submit' || b.hasAttribute('data-confirm')) return;
    if (b._kbOk) { b._kbOk = false; return; }
    e.preventDefault(); e.stopImmediatePropagation();
    ask('Are you sure? "' + text(b) + '" cannot be undone.', {danger: true, yes: text(b)})
      .then((ok) => { if (ok) { b._kbOk = true; b.click(); } });
  }, true);
  window.addEventListener('scroll', () => { if (openCombo) openCombo.place(); }, true);
  window.addEventListener('resize', () => { if (openCombo) openCombo.place(); });

  // ------------------------------------------------------------------ tables: ↑ ↓ to move, Enter to open, Space to tick
  function rowAction(r) {
    return $('[data-row-enter]', r) || $('a.btn-open[href]', r) || $('[data-add]:not(:disabled)', r)
      || $('a[href]:not([target]):not([href^="#"]):not([href^="javascript"])', r);
  }
  function navRows(tb) { return Array.from(tb.rows).filter((r) => r.dataset.kbRow && !r.hidden && visible(r)); }
  function gridSearch(tb) {
    const tbl = tb.closest('table');
    if (tbl.dataset.search) return $(tbl.dataset.search);
    const card = tbl.closest('.card');
    return (card && $$('input[type=search]', card).find(visible)) || $$('.filters input[type=search], .page-head input[type=search]', content()).find(visible) || null;
  }
  function setRoving(tb, r) { navRows(tb).forEach((x) => { x.tabIndex = x === r ? 0 : -1; }); }
  const RKEY = 'ctdms.kbrow:' + location.pathname + location.search;
  function hrefOf(r) { const a = rowAction(r); return a && a.getAttribute('href') ? new URL(a.getAttribute('href'), location.href).pathname : ''; }
  function remember(r, rows) {
    try {
      sessionStorage.setItem(RKEY, JSON.stringify({i: rows.indexOf(r), h: hrefOf(r), t: Date.now()}));
      if (hrefOf(r)) sessionStorage.setItem('ctdms.kbfrom', JSON.stringify({list: location.href, target: hrefOf(r), t: Date.now()}));
    } catch (e) { /* private mode */ }
  }
  function cameFrom() {  // opened from a list row: Esc goes back to that list with the same filters
    try {
      const s = JSON.parse(sessionStorage.getItem('ctdms.kbfrom') || 'null');
      return s && s.target === location.pathname && Date.now() - s.t < 8 * 3600e3 ? s.list : null;
    } catch (e) { return null; }
  }
  function restoreRow(force) {
    let s = null;
    try { s = JSON.parse(sessionStorage.getItem(RKEY) || 'null'); } catch (e) { return false; }
    if (!s || Date.now() - s.t > 8 * 3600e3) return false;
    let from = '';
    try { from = document.referrer ? new URL(document.referrer).pathname : ''; } catch (e) { /* ignore */ }
    const nav = performance.getEntriesByType && performance.getEntriesByType('navigation')[0];
    const backNav = nav && nav.type === 'back_forward';
    if (!force && !backNav && !(s.h && from === s.h)) return false;
    const tbl = $('.content table.kb-grid');
    if (!tbl) return false;
    const rows = navRows(tbl.tBodies[0]);
    const r = rows.find((x) => s.h && hrefOf(x) === s.h) || rows[Math.min(s.i, rows.length - 1)];
    if (!r) return false;
    setRoving(tbl.tBodies[0], r);
    r.focus();
    r.scrollIntoView({block: 'nearest'});
    return true;
  }
  function onRowKey(e) {
    const r = e.target.closest && e.target.closest('tr[data-kb-row]');
    if (!r || e.target !== r || e.altKey || e.ctrlKey || e.metaKey) return;
    const tb = r.parentElement, rows = navRows(tb), i = rows.indexOf(r);
    let to = null;
    switch (e.key) {
      case 'ArrowDown': to = rows[i + 1]; break;
      case 'ArrowUp': {
        if (i <= 0) { const s = gridSearch(tb); if (s) { e.preventDefault(); focusField(s); return; } }
        to = rows[i - 1]; break;
      }
      case 'PageDown': to = rows[Math.min(rows.length - 1, i + 10)]; break;
      case 'PageUp': to = rows[Math.max(0, i - 10)]; break;
      case 'Home': to = rows[0]; break;
      case 'End': to = rows[rows.length - 1]; break;
      case 'Enter': {
        e.preventDefault();
        const a = rowAction(r);
        if (!a) return;
        remember(r, rows);
        const after = rows[i + 1] || rows[i - 1];
        a.click();
        if (!visible(r)) {  // the row was used up (e.g. added to the gatepass): stay in the list, or go where the screen says
          if (after && visible(after)) { setRoving(tb, after); after.focus(); }
          else { const t = tb.closest('table').dataset.emptyFocus; if (t && $(t)) focusField($(t)); }
        }
        return;
      }
      case ' ': {
        const cb = $('input[type=checkbox]:not(:disabled)', r);
        if (cb) { e.preventDefault(); cb.click(); }
        return;
      }
      default:
        if (e.key.length === 1 && e.key !== '/') { const s = gridSearch(tb); if (s) focusField(s); }  // typing goes to the search box
        return;
    }
    e.preventDefault();
    if (to) { setRoving(tb, to); to.focus(); }
  }
  function setupGrids() {
    $$('table.data', content()).forEach((tbl) => {
      const tb = tbl.tBodies[0];
      if (!tb || tbl.dataset.grid === 'off') return;
      if ($('input:not([type=checkbox]):not([type=hidden]):not([type=radio]), select, textarea', tb)) return;  // editable tables keep Tab / Enter
      const rows = Array.from(tb.rows).filter((r) => rowAction(r) || $('input[type=checkbox]', r));
      if (!rows.length) return;
      tbl.classList.add('kb-grid');
      rows.forEach((r) => {
        r.dataset.kbRow = '1'; r.tabIndex = -1;
        $$('a[href], button, input[type=checkbox]', r).forEach((x) => { x.tabIndex = -1; });
      });
      const first = rows.find((r) => !r.hidden) || rows[0];
      first.tabIndex = 0;
      tb.addEventListener('keydown', onRowKey);
      tb.addEventListener('click', (e) => {  // a mouse click on a row link is remembered the same way as Enter
        const r = e.target.closest && e.target.closest('tr[data-kb-row]');
        if (r && e.target.closest('a[href]')) remember(r, navRows(tb));
      });
      tb.addEventListener('focusin', (e) => { const r = e.target.closest && e.target.closest('tr[data-kb-row]'); if (r) setRoving(tb, r); });
    });
  }
  // ↓ from a search box goes into its table
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' || e.altKey || e.ctrlKey || e.defaultPrevented) return;
    const s = e.target;
    if (!(s instanceof HTMLInputElement) || !(s.type === 'search' || s.dataset.grid) || s.id === 'quick-q') return;
    const tbl = s.dataset.grid ? $(s.dataset.grid) : ((s.closest('.card') && $('table.kb-grid', s.closest('.card'))) || $('.content table.kb-grid'));
    if (!tbl || !tbl.classList.contains('kb-grid')) return;
    const rows = navRows(tbl.tBodies[0]);
    if (rows.length) { e.preventDefault(); setRoving(tbl.tBodies[0], rows[0]); rows[0].focus(); }
  });

  // ------------------------------------------------------------------ F3: go to any screen by typing
  function goTo() {
    const items = $$('.side a[href]').map((a) => ({href: a.getAttribute('href'), text: a.textContent.trim(),
      group: ((a.closest('.group') || {}).firstElementChild || {}).textContent || ''}));
    // users with several depots: "Work in <depot>" entries (type part of the depot name, Enter) - same as the top-bar switcher
    const ds = $('#depot-switch');
    if (ds) $$('option', ds).forEach((o) => items.push({href: '', run: () => { ds.value = o.value; ds.form.submit(); },
      text: o.value ? 'Work in ' + o.textContent.trim() : 'Work in all my depots', group: 'Switch depot'}));
    if (!items.length) return;
    const l = mk('div', 'kb-layer kb-modal-wrap');
    l.id = 'kb-goto';
    l._prev = document.activeElement;
    l.innerHTML = '<div class="kb-modal kb-goto" role="dialog" aria-modal="true" aria-label="Go to a screen">'
      + '<input type="text" class="kb-goto-q" placeholder="Go to… type a screen name (e.g. gatepass, depot, city report) or an invoice no" autocomplete="off" spellcheck="false">'
      + '<div class="kb-goto-list" role="listbox"></div><div class="small muted kb-ask-hint">↑ ↓ choose · Enter open · Esc close</div></div>';
    document.body.appendChild(l);
    const q = $('.kb-goto-q', l), box = $('.kb-goto-list', l);
    let shown = [], hi = 0;
    const all = items.map((it, i) => ({...it, i, hay: norm(it.text + ' ' + it.group + ' ' + (it.href || '').replace(/[/-]/g, ' '))}));
    function draw() {
      const v = norm(q.value);
      shown = v ? all.map((it) => {
        const words = v.split(' ');
        if (!words.every((w) => it.hay.includes(w))) return null;
        const t = norm(it.text);
        return [t.startsWith(v) ? 3 : t.split(' ').some((w) => w.startsWith(words[0])) ? 2 : 1, it];
      }).filter(Boolean).sort((a, b) => b[0] - a[0] || a[1].i - b[1].i).map(([, it]) => it) : all.slice();
      if (v && $('#quick-q')) shown.push({href: '/invoices?q=' + encodeURIComponent(q.value.trim()), text: 'Find invoice / LR / gatepass “' + q.value.trim() + '”', group: 'Search'});
      hi = Math.min(hi, Math.max(0, shown.length - 1));
      box.innerHTML = shown.map((it, i) => '<div class="kb-goto-it' + (i === hi ? ' hi' : '') + '" data-i="' + i + '"><span>' + esc(it.text) + '</span><span class="muted small">' + esc(it.group) + '</span></div>').join('')
        || '<div class="combo-empty">No screen matches.</div>';
      const h = box.children[hi]; if (h) h.scrollIntoView({block: 'nearest'});
    }
    function go(i) { const it = shown[i]; if (it) { if (it.run) it.run(); else location.href = it.href; } }
    q.addEventListener('input', () => { hi = 0; draw(); });
    q.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); hi = Math.min(shown.length - 1, hi + 1); draw(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); hi = Math.max(0, hi - 1); draw(); }
      else if (e.key === 'Enter') { e.preventDefault(); go(hi); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeLayer(l); }
    });
    box.addEventListener('mousedown', (e) => e.preventDefault());
    box.addEventListener('click', (e) => { const d = e.target.closest('.kb-goto-it'); if (d) go(Number(d.dataset.i)); });
    l.addEventListener('mousedown', (e) => { if (e.target === l) closeLayer(l); });
    draw();
    q.focus();
  }

  // ------------------------------------------------------------------ hints on buttons, key bar, F1 help
  function decorate() {
    ['F2', 'F7'].forEach((k) => { if (!explicit(k)) { const t = FALLBACK[k](); if (t && !t.dataset.key) t.dataset.key = k; } });
    $$('[data-key]').forEach((b) => {
      const k = b.dataset.key.split(/\s+/)[0];
      if (!b.getAttribute('aria-keyshortcuts')) b.setAttribute('aria-keyshortcuts', k === 'Esc' ? 'Escape' : k);
      if (b.dataset.keyHint === 'off' || $('.kh', b) || !b.matches('button, a.btn')) return;
      const kb = mk('kbd', 'kh');
      kb.textContent = k; kb.setAttribute('aria-hidden', 'true');
      b.appendChild(kb);
    });
  }
  function shortLabel(t, k) {
    if (t.dataset.keyLabel) return t.dataset.keyLabel;
    if (t.matches('input, select, textarea')) return LABEL[k];
    const s = text(t);
    return s.length > 26 ? s.slice(0, 25) + '…' : (s || LABEL[k]);
  }
  function barItems() {
    const out = [];
    KEYS.forEach(([k, l]) => {
      if (k === 'F1' || k === 'F3') { out.push({k, label: l, on: true}); return; }
      if (k === 'Esc') {
        const ex = explicit('Esc'), cr = $$('.crumbs a');
        if (cameFrom()) out.push({k, label: 'Back to the list', on: true});
        else if (ex) out.push({k, label: shortLabel(ex, k), on: true});
        else if (cr.length) out.push({k, label: 'Back to ' + cr[cr.length - 1].textContent.trim(), on: true});
        else if (location.pathname !== homePath()) {
          const h = $(`.side a[href="${homePath()}"]`);
          out.push({k, label: h ? h.textContent.trim() : 'Dashboard', on: true});
        }
        return;
      }
      const t = resolve(k);
      // F6 on a button that has its own key (e.g. Generate gatepass = F8) works, but is listed once, under its own key
      if (t && k === 'F6' && t.dataset.key && !t.dataset.key.split(/\s+/).includes('F6')) return;
      if (t) out.push({k, label: shortLabel(t, k), on: enabled(t)});
    });
    return out;
  }
  let barT = null;
  function keybar() {
    const bar = $('#keybar');
    if (!bar) return;
    bar.innerHTML = barItems().map((it) => '<button type="button" tabindex="-1" class="kb-item' + (it.on ? '' : ' off') + '" data-kb="' + it.k + '"><kbd>' + it.k + '</kbd>' + esc(it.label) + '</button>').join('')
      + '<span class="kb-tip">Enter = next field · ↑↓ = move · Space = tick</span>';
    document.documentElement.style.setProperty('--kbh', bar.offsetHeight + 'px');
  }
  function keybarSoon() { clearTimeout(barT); barT = setTimeout(keybar, 120); }
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('#keybar [data-kb]');
    if (!b) return;
    const k = b.dataset.kb;
    if (k === 'Esc') back(); else run(k);
  });
  function help() {
    const l = mk('div', 'kb-layer kb-modal-wrap');
    l.id = 'kb-help';
    l._prev = document.activeElement;
    const here = barItems().filter((it) => !['F1', 'F3'].includes(it.k));
    l.innerHTML = '<div class="kb-modal kb-help" role="dialog" aria-modal="true" aria-labelledby="kb-help-t">'
      + '<div class="kb-help-head"><h2 id="kb-help-t">Keyboard - the mouse is optional</h2><button class="btn btn-sm" type="button" data-close>Close <kbd class="kh">Esc</kbd></button></div>'
      + '<div class="kb-help-cols"><div><h3>Function keys (same meaning on every screen)</h3><table class="kb-keys">'
      + KEYS.map(([k, lb, d]) => '<tr><td><kbd>' + k + '</kbd></td><td><b>' + lb + '</b> <span class="muted">' + esc(d) + '</span></td></tr>').join('')
      + '</table></div><div><h3>Moving around</h3><table class="kb-keys">'
      + '<tr><td><kbd>Enter</kbd></td><td>Next field. After the last field the cursor goes to the main button; Enter again presses it.</td></tr>'
      + '<tr><td><kbd>Shift</kbd>+<kbd>Enter</kbd></td><td>Previous field</td></tr>'
      + '<tr><td><kbd>Tab</kbd></td><td>Next field or button (<kbd>Shift</kbd>+<kbd>Tab</kbd> back)</td></tr>'
      + '<tr><td>Type</td><td>In a choice box (company, depot, customer, customer type, area, transporter, local auto): type the <b>code</b> or part of the <b>name</b>, <kbd>↓</kbd><kbd>↑</kbd> to pick, <kbd>Enter</kbd> to select</td></tr>'
      + '<tr><td><kbd>↓</kbd> <kbd>↑</kbd></td><td>Move through a table; <kbd>↓</kbd> from a search box goes into its table</td></tr>'
      + '<tr><td><kbd>Enter</kbd> on a row</td><td>Open it (on the gatepass screen: add the invoice)</td></tr>'
      + '<tr><td><kbd>Space</kbd></td><td>Tick / untick a checkbox or a ticked table row</td></tr>'
      + '<tr><td><kbd>Esc</kbd></td><td>Close the list or dialog; otherwise back one screen (asks first if something is not saved)</td></tr>'
      + '<tr><td><kbd>/</kbd></td><td>Find an invoice / LR / gatepass from any screen</td></tr>'
      + ($('#trn-panel') ? '<tr><td><kbd>Ctrl</kbd>+<kbd>Enter</kbd></td><td>Guided Training: Next / Check the step</td></tr>'
        + '<tr><td><kbd>Ctrl</kbd>+<kbd>/</kbd></td><td>Guided Training: into the panel (Tab between its buttons, Esc back)</td></tr>' : '')
      + '</table><h3>On this screen</h3><div class="kb-here">'
      + (here.length ? here.map((it) => '<span' + (it.on ? '' : ' class="off"') + '><kbd>' + it.k + '</kbd> ' + esc(it.label) + '</span>').join('') : '<span class="muted">Only the general keys.</span>')
      + '</div><p class="small muted" style="margin-top:12px">Delete, void, cancel and reject always ask first, and the safe answer (<b>No</b>) is already selected - Enter alone never deletes or cancels anything.</p></div></div></div>';
    document.body.appendChild(l);
    const btn = $('[data-close]', l);
    btn.addEventListener('click', () => closeLayer(l));
    l.addEventListener('mousedown', (e) => { if (e.target === l) closeLayer(l); });
    l.addEventListener('keydown', (e) => { if (e.key === 'Tab') { e.preventDefault(); btn.focus(); } });
    btn.focus();
  }

  // ------------------------------------------------------------------ first cursor position
  function initialFocus() {
    const a = document.activeElement;
    if (a && a !== document.body && a !== document.documentElement) { if (a._combo) focusField(a); return; }
    if (restoreRow(false)) return;
    const af = $$('[autofocus], [data-autofocus]', content()).find((x) => x._combo || visible(x));
    if (af) { focusField(af); return; }
    const mf = $('form[data-main-form]', content());
    if (mf) { const f = chain(mf)[0]; if (f) { focusField(f); return; } }
    const s = $$('.filters input[type=search], .page-head input[type=search]', content()).find(visible);
    if (s) { s.focus(); return; }
    const g = $('.content table.kb-grid');
    if (g) { const r = navRows(g.tBodies[0])[0]; if (r) r.focus(); }
  }

  // ------------------------------------------------------------------ start
  KB.ask = ask;
  KB.toast = toast;
  KB.next = (el) => move(el, 1);
  KB.focus = focusField;
  KB.jump = (target) => { const box = typeof target === 'string' ? $(target) : target; const f = box && chain(box)[0]; if (f) focusField(f); return !!f; };
  KB.refresh = keybarSoon;
  KB.clean = takeSnaps;
  KB.keys = KEYS;

  if (document.body.classList.contains('kb-off')) return;
  $$('select').filter((s) => !s.closest('.kb-layer') && wants(s)).forEach(enhance);
  setupGrids();
  decorate();
  if ($('#keybar') && !touchOnly) { document.body.classList.add('kb-on'); keybar(); }
  document.addEventListener('focusin', keybarSoon);
  document.addEventListener('change', keybarSoon);
  document.addEventListener('input', keybarSoon);
  initialFocus();
  window.addEventListener('load', () => setTimeout(takeSnaps, 0));
  window.addEventListener('pageshow', (e) => { if (e.persisted) restoreRow(true); });
})();
