// CTDMS Guided Training panel (v1.6). Explain -> highlight -> the trainee does it in the real screen -> CTDMS checks
// -> result -> continue. Keyboard: Ctrl+Enter = Next / Check · Ctrl+/ = into the panel · Esc (in the panel) = back to
// the screen. All CTDMS keys (F1-F10, Enter, arrows, Esc) keep working on the screen as usual.
(function () {
  'use strict';
  const root = document.getElementById('trn-panel');
  if (!root) return;
  const csrf = root.dataset.csrf;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  let st = null, busy = false, lastFocus = null, evalT = null, keySeen = {}, msg = null;
  const store = {get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                 set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }};

  function post(url, data) {
    const fd = new FormData();
    Object.entries(data || {}).forEach(([k, v]) => fd.append(k, v == null ? '' : v));
    return fetch(url, {method: 'POST', body: fd, headers: {'X-CSRF-Token': csrf}, credentials: 'same-origin'})
      .then((r) => r.json());
  }
  function submit(url, data) {  // a real form post (navigates)
    const f = document.createElement('form');
    f.method = 'post'; f.action = url; f.hidden = true;
    Object.entries(Object.assign({csrf_token: csrf}, data || {})).forEach(([k, v]) => {
      const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i);
    });
    document.body.appendChild(f); f.submit();
  }

  // ------------------------------------------------------------------ browser-side step checks
  function el(sel) { try { return $(sel); } catch (e) { return null; } }
  function shown(e) { return !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'); }
  function valueText(e) {
    if (!e) return '';
    if (e.tagName === 'SELECT') { const o = e.options[e.selectedIndex]; return o && o.value ? o.text : ''; }
    return e.value || '';
  }
  function holds(spec) {
    if (!spec) return false;
    for (const [k, v] of Object.entries(spec)) {
      let ok = true;
      if (k === 'path') ok = new RegExp(v).test(location.pathname);
      else if (k === 'query') ok = decodeURIComponent(location.search).toUpperCase().includes(String(v).toUpperCase());
      else if (k === 'exists') ok = shown(el(v));
      else if (k === 'absent') ok = !el(v);
      else if (k === 'visible') ok = shown(el(v));
      else if (k === 'checked') ok = !!(el(v) && el(v).checked);
      else if (k === 'focus') ok = !!(document.activeElement && document.activeElement.matches && document.activeElement.matches(v));
      else if (k === 'value') ok = new RegExp(v[1], 'i').test(valueText(el(v[0])));
      else if (k === 'option') { const s = el(v[0]); ok = !!s && Array.from(s.options || []).some((o) => new RegExp(v[1], 'i').test(o.text)); }
      else if (k === 'text') { const e = el(v[0]); ok = !!e && v.slice(1).every((t) => new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\|/g, '|'), 'i').test(e.innerText)); }
      else if (k === 'notext') { const e = el(v[0]); ok = !e || !new RegExp(v[1], 'i').test(e.innerText); }
      else if (k === 'self') ok = !st.acting;
      else if (k === 'key') ok = !!keySeen[v];
      if (!ok) return false;
    }
    return true;
  }
  function evalSoon() { clearTimeout(evalT); evalT = setTimeout(evalUi, 250); }
  function evalUi() {
    const s = st && st.step;
    if (!s || busy || !st.active || s.kind !== 'ui' || (st.needs && !st.needs.ok)) return;
    if (holds(s.ui)) act('next');
  }

  // ------------------------------------------------------------------ talking to the server
  async function load() {
    const at = encodeURIComponent(location.pathname + location.search);
    try { st = await (await fetch('/training/state?at=' + at, {credentials: 'same-origin'})).json(); } catch (e) { return; }
    render();
    if (st.step && st.step.kind === 'check' && !st.step.passed && (!st.needs || st.needs.ok)) act('check', true);
    evalSoon();
  }
  async function act(action, quiet) {
    if (busy || !st) return;
    busy = true;
    try {
      const r = await post('/training/step', {action, step_id: st.step ? st.step.id : ''});
      if (r.error) { msg = {ok: false, text: r.error}; }
      else {
        const was = st.step && st.step.id;
        st = r;
        msg = r.message || null;
        if (quiet && msg && !msg.ok) msg = {ok: null, text: msg.text};  // a check on page load that is not done yet: a hint, not an error
        if (st.step && st.step.id !== was) keySeen = {};
      }
    } finally { busy = false; }
    render();
    evalSoon();
  }

  // ------------------------------------------------------------------ panel
  let hlEls = [];
  function highlight(sel) {
    hlEls.forEach((e) => e.classList.remove('trn-hl'));
    hlEls = [];
    if (!sel) return;
    try { hlEls = $$(sel).filter(shown).slice(0, 3); } catch (e) { hlEls = []; }
    hlEls.forEach((e) => e.classList.add('trn-hl'));
  }
  function line(label, txt, cls) { return txt ? `<div class="trn-row ${cls || ''}"><span>${label}</span><p>${esc(txt)}</p></div>` : ''; }

  function render() {
    // the Guided Training page itself lists the lessons and progress - no panel needed there
    if (!st || !st.active || location.pathname === '/training') { root.hidden = true; document.body.classList.add('trn-nopanel'); return; }
    root.hidden = false;
    document.body.classList.toggle('trn-min', store.get('trn.min') === '1');
    const sm = st.summary || {};
    const L = st.lesson, S = st.step;
    let h = `<div class="trn-head"><div><b>Guided Training</b><small>${esc(st.path_name || '')}${L ? ` · lesson ${L.number} of ${L.count}` : ''}</small></div>
      <button type="button" class="trn-min-btn" data-a="min" title="Make the panel small / big">${document.body.classList.contains('trn-min') ? '⟨' : '⟩'}</button></div>
      <div class="trn-bar" role="progressbar" aria-valuenow="${sm.percent || 0}" aria-valuemin="0" aria-valuemax="100"><i style="width:${sm.percent || 0}%"></i></div>
      <div class="trn-sum">${esc(sm.done || 0)} of ${esc(sm.total || 0)} lessons completed${st.complete ? ' · <b>training complete</b>' : ''}</div>`;
    if (st.acting) h += `<div class="trn-acting">Acting as <b>${esc(st.acting)}</b> (practice only) <button type="button" class="btn btn-sm" data-a="self">Act as myself</button></div>`;
    if (!L) {
      h += `<div class="trn-body"><h3>${st.complete ? 'Training complete' : 'Choose a lesson'}</h3><p>${st.next_lesson ? 'Next: ' + esc(st.next_lesson.title) : 'All lessons of your path are done. You can replay any lesson for practice.'}</p></div>
        <div class="trn-actions"><a class="btn btn-primary" href="/training" data-primary>Lessons <kbd class="kh">Ctrl+Enter</kbd></a><button type="button" class="btn" data-a="exit">Save &amp; exit</button></div>`;
      root.innerHTML = h; bind(); return;
    }
    h += `<div class="trn-body"><h3>${esc(L.title)}</h3>`;
    if (st.needs && !st.needs.ok) {
      h += `<div class="trn-msg warn">Your practice data is not at the start of this lesson: ${esc(st.needs.msg)}.</div>
        <p class="small">Prepare it from the demonstration starting point (<b>${esc(st.needs.checkpoint)}</b>). This replaces only YOUR practice data - never live data.</p>
        <div class="trn-actions"><button type="button" class="btn btn-primary" data-a="prepare" data-primary>Prepare practice data <kbd class="kh">Ctrl+Enter</kbd></button></div></div>`;
      root.innerHTML = h + footer(L); bind(); return;
    }
    if (st.finished || !S) {
      h += `<div class="trn-msg ok">✓ Lesson complete${msg && msg.text ? ' - ' + esc(msg.text) : ''}</div>`;
      h += st.next_lesson ? `<p>Next lesson: <b>${esc(st.next_lesson.title)}</b></p><div class="trn-actions"><button type="button" class="btn btn-primary" data-a="start" data-code="${esc(st.next_lesson.code)}" data-primary>Start next lesson <kbd class="kh">Ctrl+Enter</kbd></button></div>`
        : `<p><b>You have completed your Guided Training.</b> Live work is open according to your permissions. Practice Mode stays available under Help.</p><div class="trn-actions"><button type="button" class="btn btn-primary" data-a="exit" data-primary>Leave Training Mode <kbd class="kh">Ctrl+Enter</kbd></button></div>`;
      root.innerHTML = h + '</div>' + footer(L); bind(); return;
    }
    const onPage = !S.page || location.pathname === S.page || (S.ui && S.ui.path && new RegExp(S.ui.path).test(location.pathname));
    h += `<div class="trn-stepno">Step ${L.index + 1} of ${L.steps}${S.passed ? ' · ✓ done' : ''}</div><h4>${esc(S.title)}</h4>`;
    h += line('What it does', S.what) + line('Why it matters', S.why) + line('Do this', S.do, 'do') + line('Keys', S.keys, 'keys') + line('You should see', S.expect);
    if (S.files && S.files.length) h += `<div class="trn-files">${S.files.map((f) => `<a class="btn btn-sm" href="${esc(f.url)}" download>⬇ ${esc(f.name)}</a>`).join(' ')}</div>`;
    if (!onPage && S.page) h += `<div class="trn-go">Screen: <a href="${esc(S.page)}" data-go>open ${esc(S.page)}</a></div>`;
    if (S.acting && st.acting !== S.acting) h += `<div class="trn-actions"><button type="button" class="btn" data-a="as" data-who="${esc(S.acting)}">Act as ${S.acting === 'demo.gate' ? 'Demo Gate Staff' : 'Demo Checker'}</button></div>`;
    else if ((st.demo_users || []).length && !st.acting && /checker/i.test(S.do || '')) h += `<div class="trn-actions"><button type="button" class="btn" data-a="as" data-who="demo.checker">Act as Demo Checker</button></div>`;
    if (msg && msg.text) h += `<div class="trn-msg ${msg.ok === true ? 'ok' : msg.ok === false ? 'err' : 'hint'}" role="status">${msg.ok === true ? '✓ ' : msg.ok === false ? '✗ ' : ''}${esc(msg.text)}</div>`;
    const prim = S.kind === 'check' ? 'Check' : S.kind === 'ui' ? 'Check' : 'Next';
    h += `<div class="trn-actions"><button type="button" class="btn" data-a="back" ${L.index ? '' : 'disabled'}>◀ Back</button>
      <button type="button" class="btn btn-primary" data-a="primary" data-primary>${prim} ▶ <kbd class="kh">Ctrl+Enter</kbd></button></div></div>`;
    root.innerHTML = h + footer(L);
    bind();
    highlight(S.hl);
  }
  function footer(L) {
    return `<div class="trn-foot">${L && L.video ? `<button type="button" class="btn btn-sm" data-a="watch">▶ Watch demonstration</button>` : ''}
      <button type="button" class="btn btn-sm" data-a="replay">↻ Replay lesson</button>
      <a class="btn btn-sm btn-ghost" href="/training">Lessons</a>
      <button type="button" class="btn btn-sm" data-a="exit">Save &amp; exit</button></div>
      <div class="trn-keys small muted">Ctrl+Enter next / check · Ctrl+/ panel · Esc back to the screen</div>`;
  }
  function bind() {
    $$('[data-a]', root).forEach((b) => b.addEventListener('click', () => run(b.dataset.a, b)));
  }
  function run(a, b) {
    const L = st && st.lesson;
    if (a === 'min') { store.set('trn.min', document.body.classList.contains('trn-min') ? '0' : '1'); render(); return; }
    if (a === 'back') { act('back'); return; }
    if (a === 'primary') { primary(); return; }
    if (a === 'exit') { submit('/training/exit'); return; }
    if (a === 'start') { submit('/training/start', {lesson: b.dataset.code}); return; }
    if (a === 'replay' && L) {
      (window.CTDMSKeys ? window.CTDMSKeys.ask('Start this lesson again from its first step?', {yes: 'Start again'}) : Promise.resolve(true))
        .then((ok) => { if (ok) submit('/training/start', {lesson: L.code, restart: '1'}); });
      return;
    }
    if (a === 'prepare') {
      (window.CTDMSKeys ? window.CTDMSKeys.ask('Replace your practice data with the starting point of this lesson?', {yes: 'Prepare'}) : Promise.resolve(true))
        .then((ok) => { if (ok) submit('/training/prepare'); });
      return;
    }
    if (a === 'as' || a === 'self') {
      post('/training/act-as', {who: a === 'as' ? b.dataset.who : ''}).then(() => location.reload());
      return;
    }
    if (a === 'watch' && L && L.video) { watch(L.video[0], L.video[1]); }
  }
  function primary() {
    if (!st) return;
    const p = $('[data-primary]', root);
    if (!st.step || (st.needs && !st.needs.ok) || st.finished) { if (p) p.click(); return; }
    const S = st.step;
    if (S.kind === 'read') { act('next'); return; }
    if (S.kind === 'check') { act('check'); return; }
    if (holds(S.ui)) act('next');
    else { msg = {ok: false, text: 'Not yet - do the step on the screen first (see "Do this").'}; render(); }
  }

  // ------------------------------------------------------------------ video chapter
  function watch(from, to) {
    lastFocus = document.activeElement;
    const l = document.createElement('div');
    l.className = 'kb-layer trn-video';
    l.innerHTML = `<div class="kb-modal" role="dialog" aria-modal="true" aria-label="Watch demonstration"><video controls preload="metadata" src="/training/video"></video>
      <div class="trn-actions"><button type="button" class="btn btn-primary" data-close>Back to practice <kbd class="kh">Esc</kbd></button><span class="small muted">Space play / pause · ← → 5 seconds</span></div></div>`;
    document.body.appendChild(l);
    const v = $('video', l);
    v.addEventListener('loadedmetadata', () => { v.currentTime = from; v.play().catch(() => {}); });
    v.addEventListener('timeupdate', () => { if (to && v.currentTime >= to) v.pause(); });
    const close = () => { v.pause(); l.remove(); if (lastFocus && lastFocus.focus) lastFocus.focus(); };
    $('[data-close]', l).addEventListener('click', close);
    l.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } });
    v.focus();
  }

  // ------------------------------------------------------------------ keyboard
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.altKey) {
      if ($('.kb-layer')) return;
      e.preventDefault(); e.stopPropagation();
      primary();
      return;
    }
    if (e.key === '/' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); e.stopPropagation();
      if (root.contains(document.activeElement)) { if (lastFocus && lastFocus.focus) lastFocus.focus(); return; }
      lastFocus = document.activeElement;
      document.body.classList.remove('trn-min');
      const b = $('[data-primary]', root) || $('button, a', root);
      if (b) b.focus();
      return;
    }
    if (/^F\d+$/.test(e.key)) { keySeen[e.key] = true; evalSoon(); }
  }, true);
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (lastFocus && lastFocus.focus) lastFocus.focus(); else document.body.focus(); }
  });
  root.addEventListener('focusin', (e) => { if (!root.contains(e.relatedTarget) && e.relatedTarget) lastFocus = e.relatedTarget; });
  ['input', 'change', 'click', 'keyup', 'focusin'].forEach((ev) => document.addEventListener(ev, (e) => { if (!root.contains(e.target)) evalSoon(); }, true));
  new MutationObserver(evalSoon).observe(document.body, {childList: true, subtree: true});
  load();
})();
