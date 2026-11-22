"""Two-factor sign-in codes (TOTP, RFC 6238): the 6-digit codes of Google Authenticator, Microsoft Authenticator and
similar free phone apps. Standard library only; works offline."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, t: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((time.time() if t is None else t) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify(secret: str | None, code: str | None, t: float | None = None) -> bool:
    """Accepts the code of the current 30 s step or one step either side (a phone clock a little off)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if not secret or len(code) != 6:
        return False
    now = time.time() if t is None else t
    return any(hmac.compare_digest(code_at(secret, now + d * 30), code) for d in (-1, 0, 1))


def uri(secret: str, username: str, issuer: str = "CTDMS") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"


assert code_at("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"  # RFC 6238 test vector (SHA-1, T=59), last 6 digits
