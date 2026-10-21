from __future__ import annotations

import csv
import io
import zipfile

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_db, write_block, write_transaction
from app.integrations.base import REGISTRY, get_adapter
from app.integrations.runner import field_map_for, new_api_key, run_sync
from app.models import CFA, Company, ERPSystem, IntegrationRun, Role, User, UserSession
from app.security.auth import (
    csrf_protect, hash_password, password_problems, require, sign_out_everywhere,
)
from app.security.permissions import FIXED_BASELINE, PERMISSION_GROUPS, PERMISSIONS, baseline_allows, locked_for
from app.services import audit
from app.services import settings_service as cfg
from app.services.errors import BusinessError
from app.services.import_service import TARGET_FIELDS
from app.services.utils import local_now, safe_int
from app.web.templating import flash, get_form, nav_for, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


# ----------------------------------------------------------------------------- users
@router.get("/admin/users")
def users(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.users"))):
    rows = list(db.scalars(select(User).order_by(User.username)))
    active = {uid: n for uid, n in db.execute(select(UserSession.user_id, func.count()).where(
        UserSession.revoked_at.is_(None)).group_by(UserSession.user_id))}
    return render(request, "admin/users.html", {"title": "Users", "rows": rows, "active": active})


def _user_form_ctx(db):
    return {"roles": list(db.scalars(select(Role).order_by(Role.name))),
            "companies": list(db.scalars(select(Company).order_by(Company.code))),
            "cfas": list(db.scalars(select(CFA).order_by(CFA.code)))}


@router.get("/admin/users/new")
def user_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.users"))):
    return render(request, "admin/user_form.html", {"title": "New user", "u": None, **_user_form_ctx(db)})


@router.get("/admin/users/{uid:int}")
def user_edit(uid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.users"))):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    return render(request, "admin/user_form.html", {"title": f"User {u.username}", "u": u, **_user_form_ctx(db)})


@router.post("/admin/users/save")
def user_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
              user: User = Depends(require("admin.users"))):
    uid = safe_int(form.get("id"), minimum=1)
    username = (form.get("username") or "").strip().lower()
    if not username or not form.get("full_name"):
        raise BusinessError("Username and full name are required")
    role = db.get(Role, safe_int(form.get("role_id"), 0))
    if not role:
        raise BusinessError("Select a role")
    dup = db.scalar(select(User).where(User.username == username, User.id != (uid or 0)))
    if dup:
        raise BusinessError(f"Username {username} already exists")
    # Nobody can hand out (or take over) more access than they hold: the Administrator role is only given and
    # changed by an Administrator
    admin_role = bool(role.has("*"))
    target = db.get(User, uid) if uid else None
    if (admin_role or (target and target.role and target.role.has("*"))) and not (user.role and user.role.has("*")):
        raise BusinessError("Only an Administrator can give the Administrator role or change an Administrator")
    companies = list(db.scalars(select(Company).where(Company.id.in_([i for i in (safe_int(x) for x in form.getlist("companies")) if i]))))
    cfas = list(db.scalars(select(CFA).where(CFA.id.in_([i for i in (safe_int(x) for x in form.getlist("cfas")) if i]))))
    values = {"username": username, "full_name": form.get("full_name").strip(), "email": form.get("email") or None,
              "mobile": form.get("mobile") or None, "role_id": role.id, "is_active": form.get("is_active") == "1",
              "all_companies": form.get("all_companies") == "1", "all_cfas": form.get("all_cfas") == "1"}
    if uid:
        u = db.get(User, uid)
        if not u:
            raise BusinessError("User not found")
        if u.id == user.id and (not values["is_active"] or not role.has("admin.users")):
            raise BusinessError("You cannot deactivate yourself or remove your own admin rights")
        if u.id == user.id and (u.role_id != role.id or values["all_companies"] != u.all_companies
                                or values["all_cfas"] != u.all_cfas
                                or {c.id for c in companies} != {c.id for c in u.companies}
                                or {c.id for c in cfas} != {c.id for c in u.cfas}):
            raise BusinessError("You cannot change your own role, companies or depots - ask another Administrator")
        changes = audit.diff(u, values)
        old_c, old_f = sorted(c.code for c in u.companies), sorted(c.code for c in u.cfas)
        if old_c != sorted(c.code for c in companies):
            changes["companies"] = (old_c, sorted(c.code for c in companies))
        if old_f != sorted(c.code for c in cfas):
            changes["cfas"] = (old_f, sorted(c.code for c in cfas))
        role_changed = u.role_id != role.id
        was_active = u.is_active
        for k, v in values.items():
            setattr(u, k, v)
        u.companies, u.cfas = companies, cfas
        if (not values["is_active"] and was_active) or role_changed:
            # Deactivated or given a different role: sign them out everywhere - browser sessions and API tokens,
            # for good (re-activating the account later does not revive old tokens). New role from next login.
            sign_out_everywhere(db, u)
        audit.log_changes(db, user, "USER_UPDATED", "ADMIN", changes, record_type="User", record_id=u.id, ref=u.username)
    else:
        pwd = form.get("password") or ""
        probs = password_problems(pwd)
        if probs:
            raise BusinessError("Initial password needs " + ", ".join(probs))
        u = User(**values, password_hash=hash_password(pwd), must_change_password=bool(form.get("must_change")))
        u.companies, u.cfas = companies, cfas
        db.add(u)
        db.flush()
        audit.log(db, user, "USER_CREATED", "ADMIN", record_type="User", record_id=u.id, ref=u.username,
                  details={"role": role.code})
        from app.training import progress as training
        if training.on_user_created(db, u, user):  # compulsory training ON (live database only)
            flash(request, f"{u.username} must finish Guided Training ({training.summary(training.get(db, u.id))['path_name']}) "
                           "before working on live data.", "info")
    db.commit()
    flash(request, f"User {u.username} saved.", "success")
    return redirect(f"/admin/users/{u.id}")


@router.post("/admin/users/{uid:int}/password")
def user_password(uid: int, request: Request, password: str = Form(...), must_change: str | None = Form(None),
                  db: Session = Depends(get_db), user: User = Depends(require("admin.users"))):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    probs = password_problems(password)
    if probs:
        raise BusinessError("Password needs " + ", ".join(probs))
    from app.models import utcnow
    u.password_hash = hash_password(password)
    u.must_change_password = bool(must_change)
    u.password_changed_at = utcnow()
    u.failed_logins = 0
    u.locked_until = None
    sign_out_everywhere(db, u)
    audit.log(db, user, "PASSWORD_RESET", "ADMIN", record_type="User", record_id=u.id, ref=u.username,
              details={"must_change": bool(must_change)})
    db.commit()
    flash(request, f"Password set for {u.username}" + ("; they must change it at next login." if must_change else "."),
          "success")
    return redirect(f"/admin/users/{u.id}")


# ----------------------------------------------------------------------------- roles
@router.get("/admin/roles")
def roles(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.roles"))):
    from types import SimpleNamespace
    rows = list(db.scalars(select(Role).order_by(Role.id)))
    # what each role will see in the menu (computed with the real menu rules, so this page cannot drift from them)
    menus = {r.id: nav_for(SimpleNamespace(can=lambda p, r=r: r.has(p))) for r in rows}
    people = list(db.scalars(select(User).order_by(User.username)))
    return render(request, "admin/roles.html", {
        "title": "Roles & permissions", "roles": rows, "groups": PERMISSION_GROUPS, "perms": PERMISSIONS,
        "fixed": FIXED_BASELINE, "locked": {r.id: locked_for(r.code) for r in rows}, "menus": menus, "people": people})


@router.post("/admin/roles/save")
def roles_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
               user: User = Depends(require("admin.roles"))):
    for role in db.scalars(select(Role)):
        if "*" in (role.permissions or []):
            continue
        old = sorted(role.permissions or [])
        # Fixed-baseline permissions (Master Settings, administration, Final Reports / MIS) are not decided on this
        # screen: a role that has the right keeps it, every other role never gets it, whatever the form says.
        keep_fixed = {p for p in old if p in FIXED_BASELINE and baseline_allows(role.code, p)}
        chosen = {p for p in PERMISSIONS if p not in FIXED_BASELINE and form.get(f"p_{role.id}_{p}") == "1"}
        attempted = sorted(p for p in FIXED_BASELINE if form.get(f"p_{role.id}_{p}") == "1"
                           and not baseline_allows(role.code, p))
        new = sorted(chosen | keep_fixed)
        if attempted:
            audit.log(db, user, "ROLE_PERMISSION_REFUSED", "ADMIN", record_type="Role", record_id=role.id, ref=role.code,
                      details={"fixed_baseline": attempted})
            flash(request, f"{role.name}: {', '.join(attempted)} cannot be given to this role (fixed baseline).", "warn")
        if new != old:
            added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
            role.permissions = new
            audit.log(db, user, "ROLE_PERMISSIONS_CHANGED", "ADMIN", record_type="Role", record_id=role.id, ref=role.code,
                      old=", ".join(removed) and f"removed: {', '.join(removed)}", new=added and f"added: {', '.join(added)}")
    if form.get("new_code") and form.get("new_name"):
        code = form.get("new_code").strip().upper().replace(" ", "_")
        if db.scalar(select(Role).where(Role.code == code)):
            raise BusinessError(f"Role {code} exists")
        db.add(Role(code=code, name=form.get("new_name").strip(), permissions=[], description=form.get("new_desc")))
        audit.log(db, user, "ROLE_CREATED", "ADMIN", ref=code)
    db.commit()
    flash(request, "Roles saved. Changes apply on the users' next request.", "success")
    return redirect("/admin/roles")


# ----------------------------------------------------------------------------- settings
@router.get("/admin/settings", dependencies=[Depends(write_transaction)])
def settings_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.settings"))):
    rows = cfg.all_settings(db)
    db.commit()
    return render(request, "admin/settings.html", {"title": "System settings", "rows": rows})


@router.post("/admin/settings")
def settings_save(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                  user: User = Depends(require("admin.settings"))):
    n = 0
    for s in cfg.all_settings(db):
        if s.value_type == "bool":
            if f"b_{s.key}" not in form:  # checkbox not part of the submitted form -> leave unchanged
                continue
            new = "true" if form.get(f"s_{s.key}") == "1" else "false"
        else:
            if f"s_{s.key}" not in form:
                continue
            new = (form.get(f"s_{s.key}") or "").strip()
            if s.value_type == "decimal":
                try:
                    float(new)
                except ValueError as e:
                    raise BusinessError(f"{s.key}: must be a number") from e
            if s.value_type == "int" and not new.isdigit():
                raise BusinessError(f"{s.key}: must be a whole number")
            if s.value_type == "choice" and s.choices and new not in s.choices.split(","):
                raise BusinessError(f"{s.key}: invalid choice")
        if new != (s.value or ""):
            audit.log(db, user, "SETTING_CHANGED", "ADMIN", record_type="Setting", record_id=s.key, ref=s.key,
                      field="value", old=s.value, new=new, reason=form.get("reason") or None)
            s.value = new
            s.updated_by_id = user.id
            n += 1
    db.commit()
    flash(request, f"{n} setting(s) changed.", "success")
    return redirect("/admin/settings")


# ----------------------------------------------------------------------------- integrations
@router.get("/admin/integrations")
def integrations(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.integrations"))):
    from app.integrations import adapters  # noqa: F401
    erps = list(db.scalars(select(ERPSystem).order_by(ERPSystem.code)))
    runs = list(db.scalars(select(IntegrationRun).order_by(IntegrationRun.id.desc()).limit(25)))
    return render(request, "admin/integrations.html", {"title": "Integration settings", "erps": erps, "runs": runs,
                                                       "registry": REGISTRY})


@router.post("/admin/integrations/new")
def integration_new(request: Request, code: str = Form(...), name: str = Form(...), adapter: str = Form("file"),
                    db: Session = Depends(get_db), user: User = Depends(require("admin.integrations"))):
    code = code.strip().upper()
    if db.scalar(select(ERPSystem).where(ERPSystem.code == code)):
        raise BusinessError(f"ERP {code} already exists")
    from app.integrations import adapters  # noqa: F401
    cls = REGISTRY.get(adapter)
    erp = ERPSystem(code=code, name=name.strip(), adapter=adapter, integration_mode=(cls.modes[0] if cls and cls.modes else "FILE"))
    db.add(erp)
    db.flush()
    audit.log(db, user, "ERP_CREATED", "INTEGRATION", record_type="ERPSystem", record_id=erp.id, ref=code)
    db.commit()
    return redirect(f"/admin/integrations/{erp.id}")


@router.get("/admin/integrations/{erp_id:int}")
def integration_detail(erp_id: int, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require("admin.integrations"))):
    from app.integrations import adapters  # noqa: F401
    erp = db.get(ERPSystem, erp_id)
    if not erp:
        raise HTTPException(404)
    ad = get_adapter(erp, field_map_for(db, erp))
    runs = list(db.scalars(select(IntegrationRun).where(IntegrationRun.erp_system_id == erp.id)
                           .order_by(IntegrationRun.id.desc()).limit(20)))
    return render(request, "admin/integration_detail.html", {
        "title": f"Integration {erp.code}", "erp": erp, "ad": ad, "runs": runs, "registry": REGISTRY,
        "fmap": field_map_for(db, erp), "target_fields": TARGET_FIELDS, "new_key": request.session.pop("new_api_key", None),
        "companies": list(db.scalars(select(Company).where(Company.erp_system_id == erp.id)))})


@router.post("/admin/integrations/{erp_id:int}/save")
def integration_save(erp_id: int, request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                     user: User = Depends(require("admin.integrations"))):
    from app.integrations import adapters  # noqa: F401
    erp = db.get(ERPSystem, erp_id)
    if not erp:
        raise HTTPException(404)
    old_adapter = erp.adapter
    erp.name = form.get("name") or erp.name
    erp.adapter = form.get("adapter") or erp.adapter
    erp.integration_mode = form.get("integration_mode") or erp.integration_mode
    erp.import_format = form.get("import_format") or erp.import_format
    erp.notes = form.get("notes") or None
    erp.is_active = form.get("is_active") == "1"
    erp.auto_sync = form.get("auto_sync") == "1"
    ad_cls = REGISTRY.get(erp.adapter)
    new_cfg = dict(erp.config or {})
    if ad_cls and erp.adapter == old_adapter:
        for fld in ad_cls.config_fields:
            v = form.get(f"cfg_{fld.key}")
            if v is None:
                continue
            if fld.secret and v == "********":
                continue  # unchanged secret
            new_cfg[fld.key] = v.strip()
    erp.config = new_cfg
    audit.log(db, user, "ERP_CONFIG_SAVED", "INTEGRATION", record_type="ERPSystem", record_id=erp.id, ref=erp.code,
              details={"adapter": erp.adapter, "keys": sorted(k for k, v in new_cfg.items() if v)})
    db.commit()
    flash(request, f"{erp.code} configuration saved.", "success")
    return redirect(f"/admin/integrations/{erp.id}")


@router.post("/admin/integrations/{erp_id:int}/test")
def integration_test(erp_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("admin.integrations"))):
    erp = db.get(ERPSystem, erp_id)
    ok, msg = get_adapter(erp, field_map_for(db, erp)).test_connection()
    audit.log(db, user, "ERP_TEST", "INTEGRATION", record_type="ERPSystem", record_id=erp.id, ref=erp.code,
              details={"ok": ok, "message": msg})
    db.commit()
    flash(request, f"{erp.code}: {msg}", "success" if ok else "error")
    return redirect(f"/admin/integrations/{erp.id}")


@router.post("/admin/integrations/{erp_id:int}/sync")
def integration_sync(erp_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require("admin.integrations"))):
    erp = db.get(ERPSystem, erp_id)
    run = run_sync(db, erp, user)
    db.commit()
    flash(request, f"Sync {run.status}: {run.message.splitlines()[0] if run.message else ''}",
          "success" if run.status == "SUCCESS" else ("warn" if run.status == "PARTIAL" else "error"))
    return redirect(f"/imports/{run.batch_id}" if run.batch_id else f"/admin/integrations/{erp.id}")


@router.post("/admin/integrations/{erp_id:int}/api-key")
def integration_key(erp_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require("admin.integrations"))):
    erp = db.get(ERPSystem, erp_id)
    key = new_api_key(db, erp, user)
    db.commit()
    request.session["new_api_key"] = key
    flash(request, "New API key generated - copy it now, it will not be shown again.", "warn")
    return redirect(f"/admin/integrations/{erp.id}")


# ----------------------------------------------------------------------------- data export (ownership)
@router.get("/admin/export")
def export_all(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.backup"))):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in Base.metadata.sorted_tables:
            if table.name in ("user_sessions",):
                continue
            cols = [c.name for c in table.columns if c.name not in ("password_hash", "api_key_hash", "token_hash")]
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(cols)
            for row in db.execute(select(*[table.c[c] for c in cols])):
                w.writerow(["" if v is None else v for v in row])
            zf.writestr(f"{table.name}.csv", out.getvalue())
    with write_block(db):  # all tables were read from one consistent snapshot, without the write lock
        audit.log(db, user, "FULL_DATA_EXPORT", "ADMIN", ref="all tables")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="ctdms-data-{local_now():%Y%m%d-%H%M}.zip"'})
