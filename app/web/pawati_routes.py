"""Garage Pawati register / print / receipt, LR books, Pawati numbering (v1.7 Phase 6)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CFA, DispatchSlip, GaragePawati, LRBook, PawatiPrintLog, PawatiStatus, Transporter, User
from app.security.auth import csrf_protect, require
from app.services import lr_book_service as lrb
from app.services import pawati_service as pws
from app.services.errors import BusinessError
from app.services.scope import apply_slip_scope
from app.services.utils import parse_date, safe_int
from app.web.gatepass_routes import _slip
from app.web.templating import flash, get_form, redirect, render, templates

router = APIRouter(dependencies=[Depends(csrf_protect)], include_in_schema=False)


def _pawati(db: Session, user: User, pid: int) -> GaragePawati:
    p = db.get(GaragePawati, pid)
    if not p:
        raise HTTPException(404)
    _slip(db, user, p.slip_id)  # same scope rule as the gatepass it belongs to (404 when outside it)
    return p


@router.get("/pawati")
def register(request: Request, db: Session = Depends(get_db), user: User = Depends(require("dispatch.view"))):
    qp = request.query_params
    state = qp.get("state") if qp.get("state") in ("open", "received", "unprinted", "cancelled", "all") else "open"
    stmt = select(GaragePawati).where(GaragePawati.slip_id.in_(apply_slip_scope(select(DispatchSlip.id), user)))
    if state == "open":      # printed, trip has left, receipt not recorded yet
        stmt = stmt.where(GaragePawati.status == PawatiStatus.PRINTED, GaragePawati.received_at.is_(None),
                          GaragePawati.slip_id.in_(select(DispatchSlip.id).where(DispatchSlip.gate_out_at.isnot(None))))
    elif state == "received":
        stmt = stmt.where(GaragePawati.received_at.isnot(None))
    elif state == "unprinted":
        stmt = stmt.where(GaragePawati.status == PawatiStatus.GENERATED)
    elif state == "cancelled":
        stmt = stmt.where(GaragePawati.status == PawatiStatus.CANCELLED)
    tid = safe_int(qp.get("transporter_id"))
    if tid:
        stmt = stmt.where(GaragePawati.receiving_transporter_id == tid)
    q = (qp.get("q") or "").strip()
    if q.isdigit():
        stmt = select(GaragePawati).where(GaragePawati.pawati_no == int(q),
                                          GaragePawati.slip_id.in_(apply_slip_scope(select(DispatchSlip.id), user)))
    elif q:
        stmt = stmt.where(GaragePawati.bill_nos.ilike(f"%{q}%"))
    rows = list(db.scalars(stmt.order_by(GaragePawati.id.desc()).limit(200)))
    return render(request, "pawati/list.html", {
        "title": "Garage Pawati", "rows": rows, "state": state, "q": q, "tid": tid,
        "transporters": list(db.scalars(select(Transporter).order_by(Transporter.name)))})


@router.post("/pawati/print")
def print_pawati(request: Request, form=Depends(get_form), db: Session = Depends(get_db),
                 user: User = Depends(require("dispatch.view"))):
    ids = [i for i in (safe_int(x) for x in form.getlist("ids")) if i]
    rows = [_pawati(db, user, i) for i in ids]
    if not rows:
        raise BusinessError("No Garage Pawati selected")
    pws.print_sheet(db, user, rows, form.get("reason"))
    db.commit()
    return templates.TemplateResponse(request, "pawati/print.html", {"request": request, "rows": rows})


@router.post("/pawati/{pid:int}/receive")
def receive(pid: int, request: Request, note: str = Form(""), db: Session = Depends(get_db),
            user: User = Depends(require("dispatch.view"))):
    p = _pawati(db, user, pid)
    pws.receive(db, user, p, note)
    db.commit()
    flash(request, f"Pawati {p.pawati_no}: receipt by {p.transporter.name} recorded.", "success")
    return redirect("/pawati")


@router.post("/pawati/{pid:int}/cancel")
def cancel(pid: int, request: Request, reason: str = Form(""), db: Session = Depends(get_db),
           user: User = Depends(require("dispatch.view"))):
    p = _pawati(db, user, pid)
    new = pws.cancel(db, user, p, reason)
    db.commit()
    flash(request, f"Pawati {p.pawati_no} cancelled; new Pawati {new.pawati_no} made." if new else
          f"Pawati {p.pawati_no} cancelled.", "warn")
    return redirect(f"/gatepass/{p.slip_id}")


# ----------------------------------------------------------------------------- LR books
@router.get("/lr-books")
def lr_books(request: Request, db: Session = Depends(get_db), user: User = Depends(require("lr_book.manage"))):
    ts = list(db.scalars(select(Transporter).where(Transporter.uses_lr_book.is_(True)).order_by(Transporter.name)))
    books = list(db.scalars(select(LRBook).order_by(LRBook.id.desc()).limit(50)))
    return render(request, "pawati/lr_books.html", {"title": "LR Books", "transporters": ts, "books": books,
                                                    "stats": {t.id: lrb.stats(db, t.id) for t in ts}})


@router.post("/lr-books/import")
def lr_import(request: Request, transporter_id: str = Form(""), label: str = Form(""), first_no: str = Form(""),
              last_no: str = Form(""), received_on: str = Form(""), db: Session = Depends(get_db),
              user: User = Depends(require("lr_book.manage"))):
    t = db.get(Transporter, safe_int(transporter_id) or 0)
    if not t:
        raise BusinessError("Choose the transporter")
    try:
        got = parse_date(received_on) if received_on else None
    except ValueError as e:
        raise BusinessError(str(e)) from e
    book = lrb.import_range(db, user, t, label, first_no, last_no, got)
    db.commit()
    flash(request, f"Book imported for {t.name}: {book.count} LR numbers ({book.first_no} - {book.last_no}).", "success")
    return redirect("/lr-books")


# ----------------------------------------------------------------------------- Pawati numbering (Administration)
@router.get("/admin/pawati")
def numbering(request: Request, db: Session = Depends(get_db), user: User = Depends(require("admin.settings"))):
    return render(request, "pawati/numbering.html", {
        "title": "Pawati numbering", "states": [pws.depot_state(db, c) for c in db.scalars(select(CFA).order_by(CFA.code))]})


@router.post("/admin/pawati")
def numbering_save(request: Request, cfa_id: str = Form(""), start: str = Form(""), reason: str = Form(""),
                   db: Session = Depends(get_db), user: User = Depends(require("admin.settings"))):
    cfa = db.get(CFA, safe_int(cfa_id) or 0)
    n = safe_int(start)
    if not cfa or not n:
        raise BusinessError("Choose the depot and type the number the next Pawati should get")
    pws.set_start(db, user, cfa, n, reason)
    db.commit()
    flash(request, f"The next Pawati of {cfa.code} will be number {n}.", "success")
    return redirect("/admin/pawati")
