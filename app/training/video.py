"""Chapters of the v1.6 guide video, read from its transcript - so 'Watch demonstration' always opens the right
moment, even after the video is recorded again."""
from __future__ import annotations

import re
from functools import lru_cache

from app.config import BASE_DIR

TRANSCRIPT = BASE_DIR / "docs" / "video_manual" / "v1.6" / "CTDMS_v1.6_Guide_Video_Transcript.txt"
_TS = re.compile(r"^\[(\d+):(\d+):(\d+)\]\s+(.*)$")


def _sec(h, m, s) -> int:
    return int(h) * 3600 + int(m) * 60 + int(s)


@lru_cache(maxsize=4)
def _parse(mtime: float) -> tuple[list[tuple[str, int, list[tuple[int, str]]]], int]:
    chapters, total, cur = [], 0, None
    for line in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^Total length: (\d+):(\d+):(\d+)", line)
        if m:
            total = _sec(*m.groups())
        h = re.match(r"^----- (.+) -----$", line)
        if h:
            cur = (h.group(1), None, [])
            chapters.append(cur)
            continue
        t = _TS.match(line)
        if t and cur is not None:
            cur[2].append((_sec(*t.groups()[:3]), t.group(4)))
    out = [(title, lines[0][0] if lines else 0, lines) for title, _, lines in chapters if lines]
    return out, total


def chapters() -> tuple[list, int]:
    if not TRANSCRIPT.exists():
        return [], 0
    return _parse(TRANSCRIPT.stat().st_mtime)


def resolve(spec) -> tuple[int, int] | None:
    """spec = ("Step 7",) - the chapter whose title starts with it; ("Step 2", "The freight rates") - from that line
    to the end of the chapter. Returns (start, end) seconds, or None when the video / chapter is not there."""
    if not spec:
        return None
    chs, total = chapters()
    for i, (title, start, lines) in enumerate(chs):
        if title.startswith(spec[0]):
            end = chs[i + 1][1] if i + 1 < len(chs) else (total or start + 60)
            if len(spec) > 1:
                hit = next((t for t, txt in lines if txt.startswith(spec[1])), None)
                if hit is not None:
                    start = hit
            return max(0, start - 1), end
    return None
