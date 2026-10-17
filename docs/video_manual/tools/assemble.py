"""Joins the screen recording and the narration into one MP4 and writes the transcript (txt + srt).
Run with the playwright venv python:  assemble.py <work_dir> <out_dir>"""
import glob
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg

work, out = Path(sys.argv[1]), Path(sys.argv[2])
NAME = sys.argv[3] if len(sys.argv) > 3 else "CTDMS_Master_Walkthrough"
TITLE = sys.argv[4] if len(sys.argv) > 4 else "CTDMS - MASTER WALKTHROUGH VIDEO"
steps = json.loads((work / "narration.json").read_text(encoding="utf-8"))
vids = sorted(glob.glob(str(work / "video" / "*.webm")), key=lambda p: Path(p).stat().st_size)
assert vids, "no recording found"
ff = imageio_ffmpeg.get_ffmpeg_exe()

# video length
r = subprocess.run([ff, "-i", vids[-1]], capture_output=True, text=True)
import re
m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
vlen = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
print("video seconds", vlen)

SHIFT = 0.25  # the recording starts a moment before the clock used for the timeline
rate = 22050
buf = bytearray(int((vlen + 3) * rate) * 2)
for st in steps:
    w = wave.open(str(work / "voice" / (st["id"] + ".wav")))
    data = w.readframes(w.getnframes())
    off = int((st["t"] + SHIFT) * rate) * 2
    buf[off:off + len(data)] = data[: max(0, len(buf) - off)]
    st["dur"] = w.getnframes() / w.getframerate()
with wave.open(str(work / "narration_track.wav"), "wb") as o:
    o.setnchannels(1); o.setsampwidth(2); o.setframerate(rate); o.writeframes(bytes(buf))

mp4 = out / f"{NAME}.mp4"
subprocess.run([ff, "-y", "-i", vids[-1], "-i", str(work / "narration_track.wav"), "-c:v", "libx264", "-preset", "medium",
                "-crf", "23", "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-shortest", str(mp4)], check=True, capture_output=True)
print("written", mp4, mp4.stat().st_size // 1024, "KB")


def ts(sec, srt=False):
    sec = max(0, sec)
    h, mi, s = int(sec // 3600), int(sec % 3600 // 60), sec % 60
    return f"{h:02d}:{mi:02d}:{s:06.3f}".replace(".", ",") if srt else f"{h:02d}:{mi:02d}:{int(s):02d}"


lines = [f"{TITLE}: TRANSCRIPT",
         "Prepared for Tulsyan CFA. Every record shown in the video is a DEMONSTRATION ENTRY.",
         "Total length: " + ts(vlen), "=" * 78, ""]
for st in steps:
    if st.get("title"):
        lines += ["", f"----- {st['title']} -----"]
    lines.append(f"[{ts(st['t'] + SHIFT)}]  {st['text']}")
(out / f"{NAME}_Transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

srt = []
for i, st in enumerate(steps, 1):
    a = st["t"] + SHIFT
    srt += [str(i), f"{ts(a, True)} --> {ts(a + st['dur'], True)}", st["text"], ""]
(out / f"{NAME}_Subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
print("transcript + srt written")
