"""Guided Training progress - kept in the LIVE database (table training_progress), one row per user.

Only progress lives here; the practice data lives in the trainee's own practice database (app.training.runtime).
Every management action (assign, require, reset, the ON/OFF switch) is written to the live audit log.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models import TrainingProgress, User
from app.models.base import utcnow
from app.services import audit
from app.services import settings_service as cfg
from app.training.lessons import LESSONS, PATHS, path_for_role, steps_for

NOT_STARTED, IN_PROGRESS, COMPLETED = "NOT_STARTED", "IN_PROGRESS", "COMPLETED"
STATUS_LABELS = {NOT_STARTED: "Not started", IN_PROGRESS: "In training", COMPLETED: "Completed"}
SETTING = "training.required_for_new_users"
MODULE = "TRAINING"
# open to someone who still has to finish compulsory training
OPEN_PATHS = ("/training", "/logout", "/login", "/account/password", "/static", "/health")


# ----------------------------------------------------------------------------- the gate (checked on every page)
def compulsory_on(db: Session) -> bool:
    return bool(cfg.get(db, SETTING))


def must_train(db: Session, user: User) -> bool:
    """True while compulsory training is ON and this user is marked 'training required' and has not finished."""
    if not compulsory_on(db):
        return False
    p = db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == user.id))
    return bool(p and p.required and p.status != COMPLETED)


def gate_allows(path: str, real: User) -> bool:
    if path.startswith(OPEN_PATHS):
        return True
    # the Master is never locked out of Administration (settings, users, training controls)
    return path.startswith("/admin") and (real.can("admin.settings") or real.can("admin.users"))


# ----------------------------------------------------------------------------- rows
def get(db: Session, user_id: int) -> TrainingProgress | None:
    return db.scalar(select(TrainingProgress).where(TrainingProgress.user_id == user_id))


def default_path(user: User) -> str:
    return path_for_role(user.role.code if user.role else None, user.can)


def ensure(db: Session, user: User, by: User | None = None) -> TrainingProgress:
    p = get(db, user.id)
    if p is None:
        p = TrainingProgress(user_id=user.id, path=default_path(user), required=False, status=NOT_STARTED,
                             lessons={}, current_step=0, assigned_at=utcnow(), assigned_by_id=by.id if by else None)
        db.add(p)
        db.flush()
    return p


def on_user_created(db: Session, user: User, by: User | None) -> TrainingProgress | None:
    """Called when the Master creates a user (live database only): with compulsory training ON the new user must
    finish their path first."""
    from app.training import runtime
    if runtime.active() or not compulsory_on(db):
        return None
    p = ensure(db, user, by)
    p.required = True
    audit.log(db, by, "TRAINING_REQUIRED", MODULE, record_type="User", record_id=user.id, ref=user.username,
              field="required", new=True, details={"path": p.path, "reason": "new user while compulsory training is ON"})
    return p


def lessons_of(p: TrainingProgress) -> list[str]:
    return [c for c in PATHS.get(p.path, PATHS["MANAGEMENT"])["lessons"] if c in LESSONS]


def rec(p: TrainingProgress, code: str) -> dict:
    return (p.lessons or {}).get(code) or {}


def _save(p: TrainingProgress, code: str, r: dict) -> None:
    d = dict(p.lessons or {})
    d[code] = r
    p.lessons = d
    flag_modified(p, "lessons")


def done(p: TrainingProgress, code: str) -> bool:
    return bool(rec(p, code).get("done"))


def done_count(p: TrainingProgress) -> int:
    return sum(1 for c in lessons_of(p) if done(p, c))


def percent(p: TrainingProgress | None) -> int:
    if not p:
        return 0
    n = len(lessons_of(p))
    return int(round(100 * done_count(p) / n)) if n else 0


def next_lesson(p: TrainingProgress) -> str | None:
    for c in lessons_of(p):
        if not done(p, c):
            return c
    return None


def unlocked(p: TrainingProgress, code: str) -> bool:
    """Lessons are taken in order; finished ones can always be replayed (practice mode)."""
    if code not in lessons_of(p):
        return False
    if done(p, code):
        return True
    return code == next_lesson(p)


def attempt_since(p: TrainingProgress, code: str) -> datetime:
    s = rec(p, code).get("started")
    try:
        return datetime.fromisoformat(s) if s else utcnow()
    except ValueError:
        return utcnow()


# ----------------------------------------------------------------------------- moving through a lesson
def begin_lesson(db: Session, p: TrainingProgress, code: str) -> None:
    now = utcnow()
    r = dict(rec(p, code))
    r.update(started=now.isoformat(), checks={}, attempts=int(r.get("attempts") or 0) + 1)
    _save(p, code, r)
    p.current_lesson, p.current_step = code, 0
    if p.status == NOT_STARTED:
        p.status = IN_PROGRESS
    p.started_at = p.started_at or now
    p.last_activity_at = now


def record(p: TrainingProgress, code: str, step_id: str, message: str = "") -> None:
    r = dict(rec(p, code))
    checks = dict(r.get("checks") or {})
    checks[step_id] = {"at": utcnow().isoformat(), "msg": message[:300]}
    r["checks"] = checks
    _save(p, code, r)
    p.last_activity_at = utcnow()


def passed(p: TrainingProgress, code: str, step_id: str) -> bool:
    return step_id in (rec(p, code).get("checks") or {})


def finish_lesson(db: Session, p: TrainingProgress, code: str, user) -> tuple[bool, list[str]]:
    """Mark the lesson done when every practical step of this attempt passed. Returns (done, missing steps)."""
    lesson = LESSONS[code]
    missing = [s.title for s in steps_for(lesson, user) if (s.check or s.ui) and not passed(p, code, s.id)]
    if missing:
        return False, missing
    r = dict(rec(p, code))
    now = utcnow().isoformat()
    r.setdefault("done", now)
    r["last_done"] = now
    _save(p, code, r)
    p.last_activity_at = utcnow()
    # the finished lesson stays 'current' (step = past the end) so the panel can show the completion card
    if next_lesson(p) is None and p.status != COMPLETED:
        p.status = COMPLETED
        p.completed_at = utcnow()
        real = db.get(User, p.user_id)
        audit.log(db, real, "TRAINING_COMPLETED", MODULE, record_type="User", record_id=p.user_id,
                  ref=real.username if real else None, details={"path": p.path, "lessons": len(lessons_of(p))})
    return True, []


# ----------------------------------------------------------------------------- Master controls (all audited)
def reassign(db: Session, p: TrainingProgress, path: str, by: User) -> None:
    if path not in PATHS:
        raise ValueError("unknown learning path")
    old = p.path
    p.path = path
    p.assigned_at, p.assigned_by_id = utcnow(), by.id
    if next_lesson(p) is None:
        if p.status != COMPLETED:
            p.status, p.completed_at = COMPLETED, utcnow()
    elif p.status == COMPLETED:
        p.status, p.completed_at = IN_PROGRESS, None  # a new tutorial was given on purpose
    p.current_lesson, p.current_step = next_lesson(p), 0
    audit.log(db, by, "TRAINING_REASSIGNED", MODULE, record_type="User", record_id=p.user_id, field="path",
              old=old, new=path)


def set_required(db: Session, p: TrainingProgress, required: bool, by: User) -> None:
    old = p.required
    p.required = bool(required)
    audit.log(db, by, "TRAINING_REQUIRED" if required else "TRAINING_NOT_REQUIRED", MODULE, record_type="User",
              record_id=p.user_id, field="required", old=old, new=bool(required))


def reset(db: Session, p: TrainingProgress, by: User) -> None:
    from app.training import runtime
    p.lessons, p.status, p.current_lesson, p.current_step = {}, NOT_STARTED, None, 0
    flag_modified(p, "lessons")
    p.started_at = p.last_activity_at = p.completed_at = None
    p.reset_at, p.reset_by_id = utcnow(), by.id
    runtime.remove(p.user_id)
    audit.log(db, by, "TRAINING_RESET", MODULE, record_type="User", record_id=p.user_id,
              reason="Training progress and practice data reset by the Master")


def summary(p: TrainingProgress | None) -> dict:
    if not p:
        return {"status": NOT_STARTED, "label": STATUS_LABELS[NOT_STARTED], "done": 0, "total": 0, "percent": 0}
    return {"status": p.status, "label": STATUS_LABELS.get(p.status, p.status), "done": done_count(p),
            "total": len(lessons_of(p)), "percent": percent(p), "path": p.path,
            "path_name": PATHS.get(p.path, {}).get("name", p.path)}
