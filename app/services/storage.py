"""Where uploaded files are kept: the live upload folder, or the trainee's own folder in Training Mode."""
from __future__ import annotations

from pathlib import Path


def upload_dir() -> Path:
    from app.training import runtime
    return runtime.upload_dir()
