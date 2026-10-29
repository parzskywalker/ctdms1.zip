"""Guided Training: Help › Guided Training / Practice Mode, the guidance panel's API, and the Master's controls."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import SessionLocal, get_db
from app.models import TrainingProgress, User
from app.security.auth import csrf_protect, current_user, require
from app.services import audit
from app.services import settings_service as cfg
from app.training import checks as chk
from app.training import progress as prog
from app.training import runtime, scenario
from app.training import trainee as trn_users
from app.training import video as tvideo
from app.training.lessons import LESSONS, PATHS, steps_for
from app.web.templating import flash, redirect, render

router = APIRouter(dependencies=[Depends(csrf_protect)])

VIDEO = BASE_DIR / "docs" / "video_manual" / "v1.6" / "CTDMS_v1.6_Guide_Video.mp4"
TRAINING_VIDEO = BASE_DIR / "docs" / "video_manual" / "v1.6" / "CTDMS_v1.6_Guided_Training.mp4"


def live_db(db: Session = Depends(get_db)) -> Iterator[Session]:
    """The LIVE database - training progress is always kept there, even while the screens use practice data.
    Outside Training Mode that is simply the request's own session (one SQLite writer per request, no self-lock)."""
    if runtime.active() is None:
        yield db
        return
    live = SessionLocal()
    try:
        yield live
    finally:
        live.close()


def real_user(request: Request, user: User) -> User:
    return getattr(request.state, "real_user", None) or user


def _progress(live: Session, real: User) -> TrainingProgress:
    p = prog.ensure(live, real)
    live.commit()
    return p


def _needs(lesson, uid: int, since, db: Session) -> tuple[bool, str]:
    """db = the request's practice-database session (Training Mode)."""
    if not runtime.has_practice_db(uid):
        return False, "your practice data has not been prepared yet"
    return chk.NEEDS[lesson.needs](db, chk.Ctx(since=since))


def _install(uid: int, lesson) -> None:
    scenario.ensure()
    runtime.install(uid, scenario.template_path(lesson.start))


def _enter(request: Request, uid: int) -> None:
    t = dict(request.session.get("trn") or {})
    t.update(uid=uid)
    t.setdefault("as", None)
    request.session["trn"] = t


# ----------------------------------------------------------------------------- Help › Guided Training
@router.get("/training")
def home(request: Request, user: User = Depends(current_user), live: Session = Depends(live_db)):
    real = real_user(request, user)
    p = _progress(live, real)
    rows = []
    for code in prog.lessons_of(p):
        ls = LESSONS[code]
        r = prog.rec(p, code)
        rows.append({"l": ls, "done": r.get("done"), "attempts": r.get("attempts", 0), "unlocked": prog.unlocked(p, code),
                     "video": tvideo.resolve(ls.video),
                     "current": code == (p.current_lesson or prog.next_lesson(p))})
    return render(request, "training/home.html", {
        "title": "Guided Training", "p": p, "sum": prog.summary(p), "rows": rows, "paths": PATHS,
        "active": runtime.active() is not None, "gated": prog.must_train(live, real),
        "compulsory": prog.compulsory_on(live), "has_practice": runtime.has_practice_db(real.id),
        "video": VIDEO.exists(), "training_video": TRAINING_VIDEO.exists(), "missing": scenario.missing_files()})


@router.post("/training/start")
def start(request: Request, lesson: str = Form(""), restart: str = Form(""), user: User = Depends(current_user),
          live: Session = Depends(live_db)):
    """Continue (or replay) a lesson in Training Mode - the practice database is prepared when needed."""
    real = real_user(request, user)
    p = _progress(live, real)
    cur = p.current_lesson if p.current_lesson in LESSONS else None
    if not lesson:  # Continue: the lesson in progress, or the next one after a finished lesson
        lesson = cur if cur and not prog.done(p, cur) else (prog.next_lesson(p) or cur)
    code = lesson or prog.lessons_of(p)[0]
    if code not in LESSONS or not prog.unlocked(p, code):
        flash(request, "That lesson is not open yet - lessons are taken in order.", "warn")
        return redirect("/training")
    ls = LESSONS[code]
    try:
        if not runtime.has_practice_db(real.id):
            _install(real.id, ls)
    except RuntimeError as e:
        flash(request, str(e), "error")
        return redirect("/training")
    fresh = restart == "1" or code != p.current_lesson or not prog.rec(p, code).get("started") or prog.done(p, code)
    resume_at = None if fresh else prog.rec(p, code).get("at")  # back to the screen where the trainee stopped
    if fresh:
        prog.begin_lesson(live, p, code)
        audit.log(live, real, "TRAINING_LESSON_STARTED", prog.MODULE, record_type="User", record_id=real.id,
                  ref=code, details={"path": p.path, "replay": prog.done(p, code)})
    live.commit()
    _enter(request, real.id)
    return redirect(resume_at or ls.page)


@router.post("/training/exit")
def exit_training(request: Request, user: User = Depends(current_user), live: Session = Depends(live_db)):
    real = real_user(request, user)
    request.session.pop("trn", None)
    p = prog.get(live, real.id)
    if p:
        p.last_activity_at = prog.utcnow()
        live.commit()
    flash(request, "You left Training Mode. Your progress is saved; you are working on live data again.", "info")
    return redirect("/training")


@router.post("/training/prepare")
def prepare(request: Request, user: User = Depends(current_user), live: Session = Depends(live_db),
            db: Session = Depends(get_db)):
    """Replace the practice database with the starting point of the current lesson (no restart needed)."""
    real = real_user(request, user)
    if runtime.active():
        db.close()  # release the practice file before it is replaced
    p = _progress(live, real)
    code = p.current_lesson or prog.next_lesson(p)
    if not code:
        return redirect("/training")
    ls = LESSONS[code]
    _install(real.id, ls)
    prog.begin_lesson(live, p, code)
    audit.log(live, real, "TRAINING_DATA_PREPARED", prog.MODULE, record_type="User", record_id=real.id, ref=code,
              details={"checkpoint": ls.start})
    live.commit()
    request.session["trn"] = {"uid": real.id, "as": None}
    flash(request, f"Practice data prepared for '{ls.title}' (demonstration starting point: {ls.start}).", "success")
    return redirect(ls.page)


@router.post("/training/act-as")
def act_as(request: Request, who: str = Form(""), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Two-person rule practice: let the practice database's demonstration checker act (training only)."""
    t = request.session.get("trn")
    if not t or not runtime.active():
        raise HTTPException(400, "Only in Training Mode")
    who = (who or "").strip()
    if who:
        # Only the demonstration checker (the second person of the two-person rule) and the demonstration gate staff (who
        # scan the cases and the gatepass) can be acted as. The demonstration Master / Accounts users are not offered: a
        # trainee must not get another role's screens through practice.
        ok = who in ("demo.checker", "demo.gate") and any(u.username == who for u in trn_users.demo_users(db))
        if not ok:
            raise HTTPException(400, "Unknown demonstration user")
    t = dict(t)
    t["as"] = who or None
    request.session["trn"] = t
    return JSONResponse({"ok": True, "acting": t["as"]})


# ----------------------------------------------------------------------------- the guidance panel
def _state(request: Request, live: Session, real: User, user: User, message: dict | None = None,
           db: Session | None = None) -> dict:
    p = _progress(live, real)
    t = runtime.active()
    out = {"active": t is not None, "path": p.path, "path_name": PATHS.get(p.path, {}).get("name"),
           "summary": prog.summary(p), "acting": (t or {}).get("as"), "message": message, "complete": p.status == prog.COMPLETED}
    code = p.current_lesson
    if not code or code not in LESSONS:
        out["lesson"] = None
        nxt = prog.next_lesson(p)
        out["next_lesson"] = {"code": nxt, "title": LESSONS[nxt].title} if nxt else None
        return out
    ls = LESSONS[code]
    steps = steps_for(ls, user)
    i = min(p.current_step, len(steps))
    out["lesson"] = {"code": code, "title": ls.title, "summary": ls.summary, "video": tvideo.resolve(ls.video),
                     "page": ls.page,
                     "steps": len(steps), "index": i, "number": prog.lessons_of(p).index(code) + 1 if code in prog.lessons_of(p) else 0,
                     "count": len(prog.lessons_of(p)), "source": ls.source, "done": prog.done(p, code)}
    if t:
        ok, msg = (_needs(ls, t["uid"], prog.attempt_since(p, code), db)
                   if db is not None and i == 0 and not prog.rec(p, code).get("checks") else (True, ""))
        out["needs"] = {"ok": ok, "msg": msg, "checkpoint": ls.start}
    if i >= len(steps):
        nxt = prog.next_lesson(p)
        out["finished"] = True
        out["next_lesson"] = {"code": nxt, "title": LESSONS[nxt].title} if nxt else None
        return out
    s = steps[i]
    out["step"] = {"id": s.id, "title": s.title, "what": s.what, "why": s.why, "do": s.do, "keys": s.keys,
                   "expect": s.expect, "page": s.page, "hl": s.hl, "kind": s.kind, "ui": s.ui, "acting": s.acting,
                   "files": [{"name": f, "url": f"/training/files/{f}"} for f in s.files],
                   "passed": prog.passed(p, code, s.id)}
    out["demo_users"] = ([s.acting] if s.acting else ["demo.checker"] if code in ("m03-masters", "m12-controls", "m14-bills", "a-pay")
                          else [])
    return out


def _advance(live: Session, p: TrainingProgress, user: User) -> dict | None:
    ls = LESSONS[p.current_lesson]
    steps = steps_for(ls, user)
    p.current_step = min(p.current_step + 1, len(steps))
    p.last_activity_at = prog.utcnow()
    if p.current_step >= len(steps):
        done, missing = prog.finish_lesson(live, p, ls.code, user)
        if not done:
            p.current_step = next(i for i, s in enumerate(steps) if s.title in missing)
            return {"ok": False, "text": "Not finished yet - this step still has to be done: " + missing[0]}
        return {"ok": True, "text": f"Lesson complete: {ls.title}."}
    return None


def _run_check(p: TrainingProgress, step, user: User, db: Session) -> tuple[bool, str]:
    db.expire_all()  # see what the trainee just saved
    return chk.CHECKS[step.check](db, chk.Ctx(since=prog.attempt_since(p, p.current_lesson), user=user))


def _current(p: TrainingProgress, user: User):
    if not p.current_lesson or p.current_lesson not in LESSONS:
        return None, None
    steps = steps_for(LESSONS[p.current_lesson], user)
    return (steps[p.current_step] if p.current_step < len(steps) else None), steps


@router.get("/training/state")
def state(request: Request, at: str = "", user: User = Depends(current_user), live: Session = Depends(live_db),
          db: Session = Depends(get_db)):
    real = real_user(request, user)
    if runtime.active() and at.startswith("/") and not at.startswith(("/training", "//")):
        p = prog.get(live, real.id)
        if p and p.current_lesson and prog.rec(p, p.current_lesson).get("at") != at[:300]:
            r = dict(prog.rec(p, p.current_lesson))
            r["at"] = at[:300]
            prog._save(p, p.current_lesson, r)
            live.commit()
    return JSONResponse(_state(request, live, real, user, db=db if runtime.active() else None))


@router.post("/training/step")
def step(request: Request, action: str = Form("next"), step_id: str = Form(""), user: User = Depends(current_user),
         live: Session = Depends(live_db), db: Session = Depends(get_db)):
    """next = read step done / ui step seen in the browser; check = run the server check; back = previous step."""
    if not runtime.active():
        return JSONResponse({"error": "Training Mode is not active"}, status_code=400)
    real = real_user(request, user)
    p = _progress(live, real)
    cur, steps = _current(p, user)
    msg = None
    if action == "back":
        p.current_step = max(0, p.current_step - 1)
    elif cur is None:
        pass
    elif step_id and step_id != cur.id:
        msg = {"ok": False, "text": "The guide moved on - showing the current step."}
    elif cur.check:  # never trusted from the browser: the result is looked up in the practice database
        ok, text = _run_check(p, cur, user, db)
        msg = {"ok": ok, "text": text}
        if ok:
            prog.record(p, p.current_lesson, cur.id, text)
            msg = _advance(live, p, user) or msg
    else:
        if cur.ui:
            prog.record(p, p.current_lesson, cur.id, "done on screen")
        msg = _advance(live, p, user)
    live.commit()
    return JSONResponse(_state(request, live, real, user, msg, db=db))


# ----------------------------------------------------------------------------- demonstration files and video
@router.get("/training/files/{name}")
def demo_file(name: str, user: User = Depends(current_user)):
    f = scenario.FILES.get(name)
    if not f or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, filename=name)


@router.get("/training/video")
def video(which: str = "guide", user: User = Depends(current_user)):
    f = TRAINING_VIDEO if which == "training" else VIDEO
    if not f.exists():
        raise HTTPException(404, "The guide video is not installed (docs/video_manual/v1.6)")
    return FileResponse(f, media_type="video/mp4")


# ----------------------------------------------------------------------------- Master: Administration › Guided Training
def _admin_rows(live: Session):
    from app.models import Role
    users = list(live.scalars(select(User).join(Role).order_by(User.is_active.desc(), User.full_name)))
    rows, counts = [], {"total": 0, "required": 0, prog.NOT_STARTED: 0, prog.IN_PROGRESS: 0, prog.COMPLETED: 0}
    for u in users:
        p = prog.get(live, u.id)
        s = prog.summary(p)
        if u.is_active:
            counts["total"] += 1
            counts[s["status"]] += 1
            counts["required"] += 1 if p and p.required else 0
        rows.append({"u": u, "p": p, "s": s, "path": p.path if p else prog.default_path(u),
                     "allowed": prog.allowed_paths(u),
                     "depots": "all" if u.all_cfas else ", ".join(c.code for c in u.cfas) or "-"})
    return rows, counts


@router.get("/admin/training")
def admin_training(request: Request, live: Session = Depends(live_db), user: User = Depends(require("admin.users"))):
    rows, counts = _admin_rows(live)
    return render(request, "admin/training.html", {
        "title": "Guided Training", "rows": rows, "counts": counts, "paths": PATHS, "lessons": LESSONS,
        "compulsory": prog.compulsory_on(live), "labels": prog.STATUS_LABELS})


@router.post("/admin/training/switch")
def admin_switch(request: Request, on: str = Form(""), reason: str = Form(""), live: Session = Depends(live_db),
                 user: User = Depends(require("admin.settings"))):
    old = prog.compulsory_on(live)
    new = on == "1"
    cfg.set_value(live, prog.SETTING, "true" if new else "false", user_id=user.id)
    audit.log(live, user, "SETTING_CHANGED", "ADMIN", record_type="Setting", record_id=prog.SETTING, ref=prog.SETTING,
              field=prog.SETTING, old=old, new=new, reason=(reason or "").strip() or "Guided Training switch")
    live.commit()
    flash(request, "Compulsory training is ON: users created from now on must finish their training first." if new else
          "Compulsory training is OFF: everyone works according to their permissions; training stays available "
          "under Help. Completed training is kept.", "success")
    return redirect("/admin/training")


@router.get("/admin/training/{uid:int}")
def admin_user_detail(uid: int, request: Request, live: Session = Depends(live_db), user: User = Depends(require("admin.users"))):
    u = live.get(User, uid)
    if not u:
        raise HTTPException(404)
    p = prog.get(live, uid)
    rows = []
    if p:
        for code in prog.lessons_of(p):
            r = prog.rec(p, code)
            ls = LESSONS[code]
            checks = [(s.title, (r.get("checks") or {}).get(s.id)) for s in ls.steps if s.check or s.ui]
            rows.append({"l": ls, "r": r, "checks": checks})
    history = list(live.scalars(select(audit.AuditLog).where(audit.AuditLog.module == prog.MODULE,
                                                            audit.AuditLog.record_id == str(uid))
                                .order_by(audit.AuditLog.id.desc()).limit(50)))
    return render(request, "admin/training_user.html", {"title": f"Training · {u.full_name}", "u": u, "p": p,
                                                        "sum": prog.summary(p), "rows": rows, "history": history,
                                                        "paths": PATHS})


@router.post("/admin/training/{uid:int}")
def admin_user(uid: int, request: Request, do: str = Form(...), path: str = Form(""), live: Session = Depends(live_db),
               user: User = Depends(require("admin.users"))):
    u = live.get(User, uid)
    if not u:
        raise HTTPException(404)
    p = prog.ensure(live, u, user)
    if do == "assign":
        if path not in prog.allowed_paths(u):
            flash(request, f"{u.full_name} cannot be given the {PATHS.get(path, {}).get('name', path)} tutorial - "
                           "it belongs to a different role. Change the user's role first.", "error")
            return redirect("/admin/training")
        prog.reassign(live, p, path, user)
        msg = f"{u.full_name}: learning path {PATHS[path]['name']}."
    elif do == "require":
        prog.set_required(live, p, True, user)
        msg = f"{u.full_name} must finish Guided Training before live work (while compulsory training is ON)."
    elif do == "release":
        prog.set_required(live, p, False, user)
        msg = f"{u.full_name} no longer has to finish training first."
    elif do == "reset":
        prog.reset(live, p, user)
        msg = f"{u.full_name}: training progress and practice data reset."
    else:
        raise HTTPException(400)
    live.commit()
    flash(request, msg, "success")
    return redirect("/admin/training")
