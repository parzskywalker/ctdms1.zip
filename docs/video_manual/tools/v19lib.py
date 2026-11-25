"""v1.9 user-manual videos: shared helpers. SILENT videos: every step has an English caption on screen and a Hinglish
line for the owner's own voice-over. The hold time of a step is fixed from the length of the Hinglish line (about 2.3
words per second, at least 4 s), so the owner can speak the line while the step is on screen.

Used by run_v19_manual.py (video venv, Playwright) and v19_setup.py (app venv). Never touches the real data folder."""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import demolib  # noqa: E402
from demolib import Demo  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PY = ROOT / ".venv/Scripts/python.exe"
SCRATCH = Path(r"C:\Users\Admin\AppData\Local\Temp\claude\b209c825a13a9dad\ac27c6fa-3f02-40da-8e4f-d3e721dd611c\scratchpad\manual_v19")
BASE = SCRATCH / "base"
OUTDIR = Path(r"D:\claude code\pilot_docs\videos\manual_v1.9")
WORDS_PER_SEC = 2.3

# ---- extra look for the overlay: phone-size screens get a smaller caption bar and title card
PHONE_CSS = r"""
(() => {
  const add = () => { if (!document.head || document.getElementById('__phcss')) return;
    const s = document.createElement('style'); s.id = '__phcss';
    s.textContent = `#__cap{font:600 14px/1.3 "Segoe UI",Arial,sans-serif !important;padding:8px 12px !important}
    #__badge{font-size:10px !important;top:3px !important;white-space:nowrap}
    #__title{padding:24px !important} #__title h1{font-size:28px !important} #__title p{font-size:16px !important}
    #__key{right:8px !important;font-size:18px !important;padding:6px 10px !important;bottom:calc(var(--kbh,0px) + 120px) !important}
    body{padding-bottom:130px !important}`;
    document.head.appendChild(s); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""
DESK_CSS = r"""
(() => {
  const add = () => { if (!document.head || document.getElementById('__dkcss')) return;
    const s = document.createElement('style'); s.id = '__dkcss';
    s.textContent = `#__cap{font:600 18px/1.3 "Segoe UI",Arial,sans-serif !important;padding:9px 36px !important;min-height:30px}
    #__badge{left:auto !important;right:10px !important;transform:none !important;top:56px !important;font-size:10px !important;opacity:.9}
    #__title h1{font-size:42px !important} #__title p{font-size:21px !important}
    body{padding-bottom:96px !important}`;
    document.head.appendChild(s); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


class Work:
    """One scratch folder per video: its own copy of the base practice database, its own server port."""

    def __init__(self, key: str, port: int):
        self.key, self.port = key, port
        self.dir = SCRATCH / key
        self.base_url = f"http://127.0.0.1:{port}"
        assert "data" not in {p.name for p in self.dir.parents} and self.dir.name != "data"
        self.creds = json.loads((BASE / "creds.json").read_text(encoding="utf-8"))

    def env(self) -> dict:
        e = dict(os.environ)
        d = self.dir
        e.update(PYTHONUTF8="1", PYTHONPATH=str(ROOT), CTDMS_DATABASE_URL=f"sqlite:///{(d / 'demo.db').as_posix()}",
                 CTDMS_ENVIRONMENT="development", CTDMS_SHOW_DEMO_LOGINS="false", CTDMS_DATA_DIR=str(d / "appdata"),
                 CTDMS_UPLOAD_DIR=str(d / "appdata" / "uploads"), CTDMS_BACKUP_DIR=str(d / "appdata" / "backups"),
                 CTDMS_LOG_DIR=str(d / "appdata" / "logs"))
        return e

    def kill_server(self):
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match "
                        "'uvicorn' -and $_.CommandLine -match '--port %d' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % self.port],
                       capture_output=True)
        time.sleep(1.5)

    def fresh_copy(self):
        """Copy the base practice database into this video's folder (the old copy is thrown away)."""
        self.kill_server()
        if self.dir.exists():
            for _ in range(5):
                try:
                    shutil.rmtree(self.dir)
                    break
                except OSError:
                    time.sleep(1.5)
        self.dir.mkdir(parents=True)
        shutil.copy(BASE / "demo.db", self.dir / "demo.db")
        shutil.copytree(BASE / "appdata", self.dir / "appdata")

    def start_server(self):
        self.kill_server()
        log = open(self.dir / "server.log", "a")
        subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
                         cwd=ROOT, env=self.env(), stdout=log, stderr=log)
        for _ in range(80):
            try:
                urllib.request.urlopen(self.base_url + "/health", timeout=2)
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("server did not start")

    def sql(self, q: str, args=()):
        con = sqlite3.connect(self.dir / "demo.db", timeout=20)
        try:
            return con.execute(q, args).fetchall()
        finally:
            con.close()


def hold_for(hinglish: str) -> float:
    return max(4.0, len(hinglish.split()) / WORDS_PER_SEC + 0.4)


class V19(Demo):
    """Demo with fixed hold times, English caption + Hinglish line per step, optional phone-size screen."""

    def __init__(self, base, out, record=False, speed=0.12, video_dir=None, size=(1280, 720), phone=False):
        self.phone = phone
        if phone:
            size = (390, 844)
        self.size = size
        self.steps = []
        self.n = 0
        self.say_end = 0.0
        self.record = record
        self.speed = speed
        self.base, self.out = base, Path(out)
        self.durations = {}
        self.downloads = []
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel="msedge", args=["--force-device-scale-factor=1"])
        kw = dict(viewport={"width": size[0], "height": size[1]}, accept_downloads=True)
        if phone:
            kw.update(device_scale_factor=2, is_mobile=True, has_touch=True,
                      user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")
        if record and video_dir:
            kw["record_video_dir"] = str(video_dir)
            kw["record_video_size"] = {"width": size[0], "height": size[1]}
        self.ctx = self.browser.new_context(**kw)
        self.ctx.add_init_script(demolib.OVERLAY_JS)
        self.ctx.add_init_script(PHONE_CSS if phone else DESK_CSS)
        self.pg = self.ctx.new_page()
        self.pg.set_default_timeout(20000)
        self.pg.on("dialog", lambda dlg: dlg.accept())
        self.t0 = time.time()

    # ------------------------------------------------------------------ steps
    def say(self, en: str, hi: str, title: str | None = None, hold: float | None = None, number: bool = True):
        """New step: caption on screen, the step lasts long enough to speak the Hinglish line."""
        self.sync()
        self.n += 1
        h = hold if hold else hold_for(hi) + getattr(self, 'extra', 0.0)
        txt = f"{self.n}.  {en}" if number else en
        self.steps.append({"n": self.n, "t": round(self.now(), 2), "hold": round(h, 2), "en": en, "hi": hi, "title": title})
        self.say_end = self.now() + h
        try:
            self.pg.evaluate("t => window.__setCap && window.__setCap(t)", txt)
        except Exception:
            pass
        return self.n

    def card(self, heading: str, sub: str, hi: str, hold: float | None = None):
        """Full-screen title card (chapter card), held for the Hinglish line."""
        self.sync()
        self.n += 1
        h = hold if hold else max(hold_for(hi), 5.0)
        self.steps.append({"n": self.n, "t": round(self.now(), 2), "hold": round(h, 2), "en": f"{heading} - {sub}", "hi": hi,
                           "title": heading, "card": True})
        self.say_end = self.now() + h
        self.pg.evaluate("([h,p]) => { window.__showTitle(h,p); window.__setCap(''); }", [heading, sub])
        self.sync()
        self.pg.evaluate("() => window.__showTitle('')")

    def sync(self):
        if not self.record and self.n:
            self.shot_step()  # dry run: one still of every step (the screen at the end of the step)
        if self.record:
            rest = self.say_end - self.now()
            if rest > 0:
                time.sleep(rest)

    # ------------------------------------------------------------------ quirks
    def press(self, sel: str):
        """Click a button that Playwright's visibility check misses (sticky panels); the cursor still shows."""
        self._move(self.L(sel))
        self.sync_lead(0.9)
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        try:
            with self.pg.expect_navigation(wait_until="load", timeout=10000):
                self.pg.evaluate("s => document.querySelector(s).click()", sel)
        except Exception:
            self.pg.screenshot(path=str(self.out / "stuck.png"), full_page=True)
            print("STUCK", self.pg.url, self.pg.evaluate("() => [document.querySelector('#gp-why')?.textContent, [...document.querySelectorAll('.alert')].map(a=>a.textContent.trim()).join(' | ')]"))
            raise
        self.pg.wait_for_load_state("networkidle")
        self.pause(0.7)

    def same_tab(self):
        """Print forms open a new tab in real use; in the video they open in the same window so they are seen."""
        self.pg.evaluate("() => document.querySelectorAll('form[target=_blank], a[target=_blank]').forEach(f => f.removeAttribute('target'))")

    def nav(self, label: str):
        if self.phone:
            self.go({"Check Gatepass (phone)": "/gate/phone"}[label])
            return
        self.click(f'aside.side a:has-text("{label}")')

    def sync_lead(self, lead: float = 1.0):
        """Wait until the narration of this step is nearly finished (the page is about to change)."""
        if self.record:
            rest = self.say_end - lead - self.now()
            if rest > 0:
                time.sleep(rest)

    def key(self, k: str, show: str | None = None, after: float = 0.7):
        self.sync_lead(1.2)
        for _ in range(20):
            try:
                self.pg.evaluate("t => window.__key && window.__key(t)", show or k.replace("Arrow", "").replace("Escape", "Esc"))
                break
            except Exception:
                time.sleep(0.3)
        self.pause(0.5)
        self.pg.keyboard.press(k)
        time.sleep(0.35)  # a key may start a page change: let it begin, then wait for it to end
        try:
            self.pg.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.pause(after)

    def click(self, sel: str, wait=True, nth: int | None = None):
        """Highlight the button first, press it when the narration of the step is nearly done."""
        loc = self.pg.locator(sel)
        loc = loc.nth(nth) if nth is not None else loc.first
        self._move(loc)
        self.sync_lead(0.9)
        if not self.record and self.n:
            self.shot_step()
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        loc.click()
        self.answer_yes()
        if wait:
            try:
                self.pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
        self.pause(0.7)

    def go(self, path: str, wait="load"):
        self.sync_lead(0.5)
        super().go(path, wait)

    def fill_date(self, name: str, iso: str):
        loc = self.field(name)
        self._move(loc)
        loc.fill(iso)
        self._unhl(loc)

    def type_in(self, sel: str, text: str, enter=False, delay=60):
        loc = self.L(sel)
        self._move(loc)
        loc.click()
        if self.record:
            loc.press_sequentially(text, delay=delay)
        else:
            loc.fill(text)
        if enter:
            self.pause(0.3)
            loc.press("Enter")
        self._unhl(loc)

    def sign_in(self, user: str, pw: str):
        self.go("/login")
        u = self.pg.locator("input[name=username]")
        self._move(u)
        u.click()
        u.press_sequentially(user, delay=70 if self.record else 0)
        p = self.pg.locator("input[name=password]")
        self._move(p)
        p.fill(pw)  # the field is masked (dots) on screen
        self.pause(0.5)
        self._move(self.L("button[type=submit]"))
        self.sync_lead(0.9)
        self.pg.evaluate("() => window.__pulse && window.__pulse()")
        self.pg.click("button[type=submit]")
        self.pg.wait_for_load_state("networkidle")
        self.pause(0.8)

    def sign_out(self):
        self.click('button:has-text("Sign out")')
        self.pause(0.5)

    def shot_step(self, tag: str = ""):
        """One still per step (for checking the recording afterwards)."""
        d = self.out / "stills"
        d.mkdir(parents=True, exist_ok=True)
        try:
            self.pg.screenshot(path=str(d / f"{self.n:03d}{tag}.png"))
        except Exception:
            pass


# ----------------------------------------------------------------------------- outputs
def fmt_t(sec: float, srt=False) -> str:
    sec = max(0.0, sec)
    h, m, s = int(sec // 3600), int(sec % 3600 // 60), sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",") if srt else f"{m:02d}:{int(s):02d}"


def write_srt(steps: list[dict], path: Path, shift: float, total: float):
    out = []
    for i, st in enumerate(steps):
        a = st["t"] + shift
        end = (steps[i + 1]["t"] + shift) if i + 1 < len(steps) else min(total, a + st["hold"])
        end = min(end, a + st["hold"] + 0.5)
        txt = f"{st['n']}. {st['en']}"
        out += [str(i + 1), f"{fmt_t(a, True)} --> {fmt_t(max(a + 0.5, end - 0.05), True)}", txt, ""]
    path.write_text("\n".join(out), encoding="utf-8")


SCRIPT_HTML = """<!doctype html>
<html lang="hi-Latn"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - voice-over script</title>
<style>
:root{--bg:#fff;--fg:#14202e;--mut:#5b6877;--line:#d7dee6;--card:#f4f7fa;--hi:#0b3a6b;--hibg:#e8f1fb;--chap:#0f2238;--chapfg:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#10161d;--fg:#e8eef4;--mut:#9fb0c1;--line:#2a3745;--card:#18212b;--hi:#bfe0ff;--hibg:#16283b;--chap:#0b1118;--chapfg:#e8eef4}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:20px/1.5 "Segoe UI",system-ui,Arial,sans-serif;padding:16px;max-width:1100px;margin:auto}
h1{font-size:1.5rem;margin:.2em 0}.sub{color:var(--mut);font-size:.9rem;margin-bottom:14px}
table{width:100%;border-collapse:collapse}th{position:sticky;top:0;background:var(--card);text-align:left;font-size:.8rem;color:var(--mut);padding:8px;border-bottom:2px solid var(--line)}
td{vertical-align:top;padding:10px 8px;border-bottom:1px solid var(--line)}td.t{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--mut);font-size:.85rem;width:74px}
td.s{color:var(--mut);font-size:.9rem;width:36%}td.b{font-size:1.18rem;font-weight:600;color:var(--hi);background:var(--hibg)}
tr.chap td{background:var(--chap);color:var(--chapfg);font-weight:700;font-size:1.05rem}
.n{display:inline-block;background:var(--line);border-radius:10px;padding:0 7px;font-size:.75rem;margin-right:6px;color:var(--fg)}
@media (max-width:700px){table,tbody,tr,td{display:block}thead{display:none}tr{border-bottom:2px solid var(--line);padding:8px 0}td{border:0;padding:4px 6px}td.s,td.t{width:auto}}
</style></head><body>
<h1>__TITLE__</h1>
<div class="sub">Voice-over script (Hinglish). Video length __LEN__ - __COUNT__ steps. Each step stays on screen long enough to read the line aloud at a calm pace. Words in English are the names on the screen.</div>
<table><thead><tr><th>Time</th><th>Screen (English caption)</th><th>Bolna hai (Hinglish)</th></tr></thead><tbody>
__ROWS__
</tbody></table></body></html>
"""


def write_script_html(steps: list[dict], path: Path, title: str, shift: float, total: float):
    rows = []
    for st in steps:
        if st.get("title") and not st.get("card") and st.get("chapter_row"):
            pass
        t = fmt_t(st["t"] + shift)
        if st.get("card"):
            rows.append(f'<tr class="chap"><td class="t">{t}</td><td class="s" style="color:inherit">{html.escape(st["title"])}</td>'
                        f'<td class="b" style="background:transparent;color:inherit">{html.escape(st["hi"])}</td></tr>')
        else:
            rows.append(f'<tr><td class="t">{t}</td><td class="s"><span class="n">{st["n"]}</span>{html.escape(st["en"])}</td>'
                        f'<td class="b">{html.escape(st["hi"])}</td></tr>')
    page = (SCRIPT_HTML.replace("__TITLE__", html.escape(title)).replace("__LEN__", fmt_t(total))
            .replace("__COUNT__", str(len(steps))).replace("__ROWS__", "\n".join(rows)))
    path.write_text(page, encoding="utf-8")


def encode_mp4(webm: Path, mp4: Path, phone=False) -> float:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ff, "-y", "-i", str(webm), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p", "-r", "25",
           "-movflags", "+faststart"]
    if phone:
        cmd += ["-vf", "scale=-2:700:flags=lanczos,pad=1280:720:(ow-iw)/2:10:color=0x0f2238"]
    else:
        cmd += ["-vf", "scale=1280:720:flags=lanczos"]
    subprocess.run(cmd + [str(mp4)], check=True, capture_output=True)
    r = subprocess.run([ff, "-i", str(mp4)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def extract_stills(mp4: Path, steps: list[dict], shift: float, folder: Path):
    """One still per step, taken 2.5 s after the step starts (or at its middle if shorter)."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*.png"):
        f.unlink()
    for st in steps:
        at = st["t"] + shift + min(2.5, st["hold"] / 2)
        subprocess.run([ff, "-y", "-ss", f"{at:.2f}", "-i", str(mp4), "-frames:v", "1", str(folder / f"{st['n']:03d}.png")],
                       capture_output=True)
