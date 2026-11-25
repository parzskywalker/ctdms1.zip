// State -> City -> PIN on the master forms (v1.8.8). The state's places come from /static/geo/<state>.json (India Post
// open data, built by deploy/build_geo.py). keys.js turns both <select>s into type-to-search boxes (arrows, Enter).
// A city option's value is "place|pin" so places with the same name stay apart; the server keeps only the place.
(function () {
  'use strict';
  const st = document.querySelector('select[data-geo-state]');
  const ct = document.querySelector('select[data-geo-city]');
  const pin = document.querySelector('input[data-geo-pin]');
  if (!st || !ct || ct.disabled) return;
  const cache = {};
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  async function load(state) {
    if (!state) return null;
    const k = slug(state);
    if (!(k in cache)) {
      try { const r = await fetch('/static/geo/' + k + '.json'); cache[k] = r.ok ? await r.json() : null; } catch (e) { cache[k] = null; }
    }
    return cache[k];
  }
  function add(value, text, pinCode, keys) {
    const o = document.createElement('option');
    o.value = value; o.textContent = text;
    if (pinCode) o.dataset.pin = pinCode;
    if (keys) o.dataset.k = keys;
    return o;
  }
  async function fill() {
    const d = await load(st.value), cur = ct.dataset.current || '';
    const frag = document.createDocumentFragment();
    frag.appendChild(add('', d ? '— type the city / district —' : '— choose the state first —'));
    if (cur) frag.appendChild(add(cur, cur));
    if (d) {
      for (const [place, dist, code] of d.places) {
        frag.appendChild(add(place + '|' + code,
          place + (place === dist ? ' — district' : ' — ' + dist + ' district') + ' — ' + code, code, dist + ' ' + code));
      }
    }
    ct.replaceChildren(frag);
    ct.value = cur;
  }
  st.addEventListener('change', () => { ct.dataset.current = ''; fill(); });
  ct.addEventListener('change', () => {
    const o = ct.selectedOptions[0];
    if (o && o.dataset.pin && pin) pin.value = o.dataset.pin;
  });
  fill();
})();
