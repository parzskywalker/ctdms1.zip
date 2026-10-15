"""Single sign-on with OpenID Connect - Google Workspace, Microsoft Entra ID (Azure AD), Okta, Keycloak ...

Set in .env: CTDMS_OIDC_ISSUER, CTDMS_OIDC_CLIENT_ID, CTDMS_OIDC_CLIENT_SECRET (and optionally CTDMS_OIDC_LABEL).
Register `<public url>/login/sso/callback` as the redirect URI at the identity provider.

Users are NOT created automatically: the e-mail address in the signed id-token must belong to exactly one active
CTDMS user (Administration › Users). Roles, companies and CFAs stay managed in CTDMS. Password login keeps working.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, utcnow
from app.services.errors import BusinessError

_discovery: dict = {}


def _http():
    import httpx
    return httpx


def discovery() -> dict:
    s = get_settings()
    hit = _discovery.get(s.oidc_issuer)
    if hit and hit[0] > time.time():
        return hit[1]
    url = s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    r = _http().get(url, timeout=15)
    if r.status_code != 200:
        raise BusinessError(f"Sign-in provider not reachable ({r.status_code})")
    data = r.json()
    _discovery[s.oidc_issuer] = (time.time() + 3600, data)
    return data


def start(redirect_uri: str) -> tuple[str, dict]:
    """URL to send the browser to, and what to remember in the session."""
    s = get_settings()
    d = discovery()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    url = d["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": s.oidc_client_id, "redirect_uri": redirect_uri,
        "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account"})
    return url, {"state": state, "nonce": nonce, "redirect_uri": redirect_uri, "ts": int(time.time())}


def _verify(id_token: str, nonce: str) -> dict:
    import jwt
    s = get_settings()
    d = discovery()
    key = jwt.PyJWKClient(d["jwks_uri"]).get_signing_key_from_jwt(id_token).key
    try:
        claims = jwt.decode(id_token, key, algorithms=["RS256", "ES256", "PS256"], audience=s.oidc_client_id,
                            issuer=d.get("issuer") or s.oidc_issuer, leeway=60)
    except jwt.PyJWTError as e:
        raise BusinessError(f"Sign-in token rejected ({type(e).__name__})") from e
    if claims.get("nonce") != nonce:
        raise BusinessError("Sign-in token does not belong to this login attempt")
    return claims


def finish(code: str, state: str, saved: dict | None) -> dict:
    """Exchange the code for a verified id-token; returns its claims."""
    s = get_settings()
    if not saved or not state or not secrets.compare_digest(str(saved.get("state", "")), str(state)):
        raise BusinessError("Sign-in expired or was started in another window - please try again")
    if time.time() - int(saved.get("ts", 0)) > 600:
        raise BusinessError("Sign-in took too long - please try again")
    d = discovery()
    r = _http().post(d["token_endpoint"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": saved["redirect_uri"],
        "client_id": s.oidc_client_id, "client_secret": s.oidc_client_secret}, timeout=20)
    if r.status_code != 200 or "id_token" not in r.json():
        raise BusinessError(f"Sign-in provider refused the login ({r.status_code})")
    return _verify(r.json()["id_token"], saved["nonce"])


def user_for(db: Session, claims: dict) -> User:
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    if not email or "@" not in email:
        raise BusinessError("Your company account did not share an e-mail address")
    if claims.get("email_verified") is False:
        raise BusinessError("Your e-mail address is not verified at the sign-in provider")
    users = list(db.scalars(select(User).where(func.lower(User.email) == email)))
    if len(users) != 1:
        raise BusinessError(f"{email} is not set up in CTDMS" if not users else
                            f"{email} belongs to more than one CTDMS user - ask the administrator")
    u = users[0]
    if not u.is_active:
        raise BusinessError("Your CTDMS account is deactivated")
    if u.locked_until and u.locked_until > utcnow():
        raise BusinessError("Your CTDMS account is locked for a few minutes")
    return u
