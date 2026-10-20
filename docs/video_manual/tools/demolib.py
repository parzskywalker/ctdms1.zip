"""Helpers for the CTDMS demonstration walkthrough: drives the real web UI with Playwright (Microsoft Edge),
shows captions + a visible cursor, and (in record mode) times every step to a narration audio file.

Used only to produce the video manual. It never touches the production database: the caller points the app at a
separate, clean database file."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

OVERLAY_JS = r"""
(() => {
  if (window.top !== window) return;
  const css = `
  #__cap{position:fixed;left:0;right:0;bottom:var(--kbh,0px);z-index:2147483646;background:rgba(15,34,56,.94);color:#fff;
    font:600 19px/1.35 "Segoe UI",Arial,sans-serif;padding:12px 40px;text-align:center;pointer-events:none;display:none}
  #__badge{position:fixed;left:50%;transform:translateX(-50%);top:6px;z-index:2147483646;background:#b45309;color:#fff;font:700 12px "Segoe UI";
    padding:4px 10px;border-radius:12px;pointer-events:none;letter-spacing:.4px}
  #__cur{position:fixed;z-index:2147483647;width:26px;height:26px;border:3px solid #e11d48;border-radius:50%;
    background:rgba(225,29,72,.18);pointer-events:none;left:-60px;top:-60px;transition:left .5s ease,top .5s ease,transform .15s}
  #__cur.click{transform:scale(.6)}
  .__hl{outline:3px solid #e11d48 !important;outline-offset:2px;transition:outline .2s}
  #__title{position:fixed;inset:0;z-index:2147483647;background:#0f2238;color:#fff;display:none;flex-direction:column;
    align-items:center;justify-content:center;font-family:"Segoe UI",Arial,sans-serif;text-align:center;padding:60px}
  #__title h1{font-size:46px;margin:0 0 14px;color:#fff} #__title p{font-size:22px;color:#b9d3ee;max-width:980px;margin:0}
  #__key{position:fixed;right:28px;bottom:calc(var(--kbh,0px) + 92px);z-index:2147483647;display:none;align-items:center;gap:10px;
    background:#fff;color:#18212d;border:2px solid #18212d;border-bottom-width:6px;border-radius:10px;padding:10px 18px;
    font:700 26px/1 "Segoe UI",Arial,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.35);pointer-events:none}
  #__key small{font:600 13px "Segoe UI";color:#5f6b7a;text-transform:uppercase;letter-spacing:.5px}
  #__kbonly{position:fixed;left:calc(50% + 140px);top:6px;z-index:2147483646;background:#0f766e;color:#fff;font:700 12px "Segoe UI";
    padding:4px 10px;border-radius:12px;pointer-events:none;letter-spacing:.4px;display:none}
  body{padding-bottom:78px !important}`;
  function build() {
    if (document.getElementById('__cap') || !document.body) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    for (const id of ['__cap', '__badge', '__cur', '__title', '__key', '__kbonly']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
    document.getElementById('__badge').textContent = 'DEMONSTRATION DATA - NOT REAL';
    document.getElementById('__kbonly').textContent = 'KEYBOARD ONLY - NO MOUSE';
    try { if (sessionStorage.getItem('__kbonly') === '1') document.getElementById('__kbonly').style.display = 'block'; } catch (e) {}
    let c = null, t = null;
    try { c = sessionStorage.getItem('__cap'); t = sessionStorage.getItem('__title'); } catch (e) {}
    if (c) { const e = document.getElementById('__cap'); e.textContent = c; e.style.display = 'block'; }
    if (t) { const o = JSON.parse(t); window.__showTitle(o[0], o[1]); }
    try { const p = sessionStorage.getItem('__cur'); if (p) { const q = JSON.parse(p); const k = document.getElementById('__cur'); k.style.transition = 'none'; k.style.left = q[0] + 'px'; k.style.top = q[1] + 'px'; setTimeout(() => k.style.transition = '', 30); } } catch (e) {}
  }
  window.__setCap = (txt) => { try { sessionStorage.setItem('__cap', txt || ''); } catch (e) {}
    const e = document.getElementById('__cap'); if (!e) return; e.textContent = txt || ''; e.style.display = txt ? 'block' : 'none'; };
  window.__showTitle = (h, p) => { const e = document.getElementById('__title'); if (!e) return;
    if (!h) { e.style.display = 'none'; try { sessionStorage.removeItem('__title'); } catch (x) {} return; }
    e.innerHTML = '<h1></h1><p></p>'; e.firstChild.textContent = h; e.lastChild.textContent = p || ''; e.style.display = 'flex';
    try { sessionStorage.setItem('__title', JSON.stringify([h, p || ''])); } catch (x) {} };
  window.__moveCur = (x, y) => { const k = document.getElementById('__cur'); if (!k) return; k.style.left = (x - 13) + 'px'; k.style.top = (y - 13) + 'px';
    try { sessionStorage.setItem('__cur', JSON.stringify([x - 13, y - 13])); } catch (e) {} };
  let keyT = null;
  window.__key = (k) => { const e = document.getElementById('__key'); if (!e) return; e.innerHTML = '<small>key</small><span></span>';
    e.lastChild.textContent = k; e.style.display = 'flex'; clearTimeout(keyT); keyT = setTimeout(() => { e.style.display = 'none'; }, 1300); };
  window.__kbOnly = (on) => { try { sessionStorage.setItem('__kbonly', on ? '1' : ''); } catch (x) {}
    const e = document.getElementById('__kbonly'); if (e) e.style.display = on ? 'block' : 'none'; };
  window.__pulse = () => { const k = document.getElementById('__cur'); if (!k) return; k.classList.add('click'); setTimeout(() => k.classList.remove('click'), 180); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();
"""


class Demo:
    def __init__(self, base: str, out: Path, record: bool = False, durations: dict | None = None,
                 speed: float = 1.0, video_dir: Path | None = None, size=(1366, 768)):
        self.base, self.out, self.record = base, Path(out), record
        self.durations = durations or {}
        self.speed = speed  # <1 = faster (dry runs)
        self.steps: list[dict] = []
        self.n = 0
        self.t0 = None
        self.say_end = 0.0
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="msedge", args=["--force-device-scale-factor=1"])
        kw = dict(viewport={"width": size[0], "height": size[1]}, accept_downloads=True)
        if record and video_dir:
            kw["record_video_dir"] = str(video_dir)
            kw["record_video_size"] = {"width": size[0], "height": size[1]}
        self.ctx = self.browser.new_context(**kw)
        self.ctx.add_init_script(OVERLAY_JS)
        self.pg = self.ctx.new_page()
        self.pg.set_default_timeout(20000)
        self.pg.on("dialog", lambda dlg: dlg.accept())
        self.t0 = time.time()
        self.downloads: list[str] = []

    # ------------------------------------------------------------------ timing / narration
    def now(self) -> float:
        return time.time() - self.t0

    def sync(self):
        """Wait until the narration of the last step has finished."""
        if self.record:
            rest = self.say_end - self.now()
            if rest > 0:
                time.sleep(rest)

    def say(self, text: str, title: str | None = None):
        """Start a narrated step: caption on screen, narration scheduled."""
        self.sync()
        self.n += 1
        sid = "s%03d" % self.n
        dur = self.durations.get(sid, 0)
        self.steps.append({"id": sid, "t": round(self.now(), 2), "text": text, "title": title})
        self.say_end = self.now() + (dur + 0.6 if dur else 0)
        try:
            self.pg.evaluate("t => window.__setCap && window.__setCap(t)", text)
        except Exception:
            pass
        return sid

    def pause(self, s: float = 0.8):
        time.sleep(s * (1 if self.record else self.speed))

    def title_card(self, heading: str, sub: str, narration: str):
        self.say(narration, title=heading)
        self.pg.evaluate("([h,p]) => window.__showTitle(h,p)", [heading, sub])
        self.pg.evaluate("t => window.__setCap('')", "")
        self.sync()
        self.pg.evaluate("() => window.__showTitle('')")

    # ------------------------------------------------------------------ navigation / actions
    def go(self, path: str, wait="load"):
        self.pg.goto(self.base + path if path.startswith("/") else path, wait_until=wait)
        self.pg.wait_for_load_state("networkidle")
        self.pause(0.6)

    def _move(self, loc):
        try:
            loc.scroll_into_view_if_needed(timeout=5000)
            box = loc.bounding_box()
            if not box:
                return
            x, y = box["x"] + min(box["width"] / 2, 120), box["y"] + box["height"] / 2
            self.pg.evaluate("([x,y]) => window.__moveCur && window.__moveCur(x,y)", [x, y])
            self.pause(0.55)
            loc.evaluate("e => e.classList.add('__hl')")
        except Exception:
            pass

    def _unhl(self, loc):
        try:
            loc.evaluate("e => e.classList.remove('__hl')")
        except Exception:
            pass

    def L(self, sel: str):
        return self.pg.locator(sel).first

    def field(self, name: str):
        return self.pg.locator(f'main [name="{name}"]').first

    def fill(self, name: str, value, delay: int = 25):
        loc = self.field(name)
        self._move(loc)
        if self.record and len(str(value)) < 60:
            loc.click()
            loc.press("Control+a")  # type over what is there (a pre-filled amount is replaced, not appended to)
            loc.press_sequentially(str(value), delay=delay)
        else:
            loc.fill(str(value))
        self._unhl(loc)

    def select(self, name: str, label: str | None = None, value: str | None = None):
        loc = self.field(name)
        self._move(loc)
        if value is not None:
            loc.select_option(value=value)
        else:
            opts = loc.evaluate("e => [...e.options].map(o => [o.value, o.text])")
            hit = next((v for v, t in opts if t.strip() == label), None) or \
                  next((v for v, t in opts if label.lower() in t.lower()), None)
            if hit is None:
                raise RuntimeError(f"option '{label}' not in {name}: {opts}")
            if loc.evaluate("e => !!e._combo"):
                self._type_combo(loc, hit, label)
            else:
                loc.select_option(value=hit)
        self._unhl(loc)

    def _type_combo(self, loc, value: str, label: str):
        """v1.6 searchable choice box: type part of the name, Enter picks the highlighted entry."""
        inp = self.pg.locator("#" + loc.evaluate("e => e._combo.inp.id"))
        self._move(inp)
        inp.click()
        q = " ".join(re.split(r" · |\(", label)[0].split()[:2])
        inp.press_sequentially(q, delay=45 if self.record else 0)
        self.pause(0.5)
        if self.pg.evaluate("v => { const c = document.querySelector('.combo-list:not([hidden]) .combo-opt.hi'); "
                            "return !!c; }", value):
            hi_val = self.pg.evaluate("sel => { const c = sel._combo; return c.shown[c.hi] ? c.shown[c.hi].value : null; }",
                                      loc.element_handle())
            if hi_val == value:
                inp.press("Enter")
                return
        loc.select_option(value=value)
        inp.evaluate("e => e.blur()")
        self.pause(0.3)

    def check(self, name: str, on: bool = True):
        loc = self.field(name)
        self._move(loc)
        loc.set_checked(on)
        self._unhl(loc)

    def tick_label(self, name: str, label_part: str):
        """Tick a checkbox from a group (same name) whose visible label contains label_part."""
        boxes = self.pg.locator(f'main input[name="{name}"]')
        for i in range(boxes.count()):
            b = boxes.nth(i)
            txt = b.evaluate("e => (e.closest('label') || e.parentElement).innerText")
            if label_part.lower() in txt.lower():
                self._move(b)
                b.set_checked(True)
                self._unhl(b)
                return
        raise RuntimeError(f"no checkbox {name} with label {label_part}")

    def radio(self, name: str, value: str):
        loc = self.pg.locator(f'main input[name="{name}"][value="{value}"]').first
        lab = loc.locator("xpath=ancestor::label[1]")
        tgt = lab if lab.count() else loc
        tgt.scroll_into_view_if_needed()
        self._move(tgt)
        try:
            loc.check(force=True, timeout=4000)
        except Exception:  # covered by a sticky panel: set it the way a click would
            loc.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true})); }")
        self._unhl(tgt)

    def row_btn(self, row_text: str, btn_sel: str):
        loc = self.pg.locator(f'main tr:has-text("{row_text}") {btn_sel}').first
        self._move(loc)
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        loc.click()
        self.pause(0.6)

    def post_file(self, path: str, form: dict, filename: str):
        """Save what a button would download (PDF / Excel) without leaving the page."""
        tok = self.pg.locator('input[name=csrf_token]').first.input_value()
        r = self.ctx.request.post(self.base + path, form={"csrf_token": tok, **form})
        target = self.out / "exports" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.body())
        self.downloads.append(str(target))
        return r.status

    def get_file(self, path: str, filename: str):
        r = self.ctx.request.get(self.base + path)
        target = self.out / "exports" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.body())
        self.downloads.append(str(target))
        return r.status

    def click(self, sel: str, wait=True, nth: int | None = None):
        loc = self.pg.locator(sel)
        loc = loc.nth(nth) if nth is not None else loc.first
        self._move(loc)
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        loc.click()
        self.answer_yes()
        if wait:
            try:
                self.pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        self.pause(0.7)

    def answer_yes(self):
        """v1.6 asks 'are you sure' in its own Yes / No box (keyboard friendly) instead of the browser's."""
        try:
            if not self.pg.locator("#kb-ask").count():
                return
        except Exception:
            return
        yes = self.pg.locator("#kb-ask [data-yes]").first
        self.pause(0.8)
        self._move(yes)
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        yes.click()

    # ------------------------------------------------------------------ keyboard-only part of the video
    def kb_only(self, on: bool = True):
        self.pg.evaluate("on => { window.__kbOnly && window.__kbOnly(on); if (on && window.__moveCur) window.__moveCur(-200, -200); }", on)

    def key(self, k: str, show: str | None = None, after: float = 0.7):
        """Press one key and show it on screen (keycap in the corner)."""
        self.pg.evaluate("t => window.__key && window.__key(t)", show or k.replace("Arrow", "").replace("Escape", "Esc"))
        self.pause(0.35)
        self.pg.keyboard.press(k)
        self.pause(after)

    def type_keys(self, text: str, after: float = 0.6):
        self.pg.evaluate("t => window.__key && window.__key(t)", "typing  " + text)
        self.pg.keyboard.type(text, delay=120 if self.record else 0)
        self.pause(after)

    def btn(self, text: str, **kw):
        self.click(f'main button:has-text("{text}"), main a:has-text("{text}"), main input[type=submit][value*="{text}"]', **kw)

    def upload(self, name_or_sel: str, path: str):
        loc = self.pg.locator(name_or_sel).first
        self._move(loc)
        loc.set_input_files(path)
        self.pause(0.5)

    def download(self, sel: str, filename: str, nth: int | None = None):
        loc = self.pg.locator(sel)
        loc = loc.nth(nth) if nth is not None else loc.first
        self._move(loc)
        with self.pg.expect_download() as dl:
            loc.click()
        target = self.out / "exports" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        dl.value.save_as(str(target))
        self.downloads.append(str(target))
        self.pause(0.5)
        return target

    def text(self) -> str:
        return self.pg.inner_text("main")

    def shot(self, name: str, full: bool = False):
        d = self.out / "shots"
        d.mkdir(parents=True, exist_ok=True)
        if full:
            self.pg.evaluate("() => { window.scrollTo(0,0); const c=document.getElementById('__cap'); if(c) c.style.visibility='hidden'; }")
        self.pg.screenshot(path=str(d / f"{name}.png"), full_page=full)
        if full:
            self.pg.evaluate("() => { const c=document.getElementById('__cap'); if(c) c.style.visibility='visible'; }")

    def flash_text(self) -> str:
        try:
            return self.pg.locator(".alert, .flash, .toast").first.inner_text(timeout=1500)
        except Exception:
            return ""

    def login(self, user: str, pw: str):
        self.go("/login")
        self.fill("username", user) if self.pg.locator('main [name=username]').count() else self.pg.fill('input[name=username]', user)
        self.pg.fill('input[name=password]', pw)
        self.pg.click('button[type=submit]')
        self.pg.wait_for_load_state("networkidle")
        self.pause(0.8)

    def logout(self):
        self.pg.locator('button:has-text("Sign out")').first.click()
        self.pg.wait_for_load_state("networkidle")

    def finish(self):
        self.sync()
        vid = None
        try:
            vid = self.pg.video.path() if self.record else None
        except Exception:
            pass
        self.ctx.close()
        self.browser.close()
        self.pw.stop()
        return vid


def wav_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())
