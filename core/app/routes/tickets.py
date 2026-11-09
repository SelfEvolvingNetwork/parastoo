"""مسیرهای تیکتینگ عضو‌به‌عضو (v0.5.17) — /api/tickets* — لایهٔ نازک.

همهٔ قواعد در services/tickets.py است؛ اینجا فقط احراز هویت، CSRF انسان،
نگاشت خطا به HTTP و JSON. دو نوع فراخواننده:
- انسان: کوکی نشست پنل (کاربر/ادمین) + CSRF در POST (هدر X-CSRF-Token)
- عامل پروژه: توکن پل `pst_` (Bearer/Basic — همان قرارداد پل) + باکت نرخ + audit
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from ..db import get_db
from ..deps import resolve_user
from ..models import User
from ..services import bridge as bsvc
from ..services import tickets as tsvc
from ..services.tickets import TicketError
from ..security import check_csrf

router = APIRouter()

# نگاشت کد خطا → وضعیت HTTP (IDOR-safe: نقض دسترسی همیشه 404)
_HTTP_FOR = {
    "bad_target": 400, "bad_box": 400, "schema": 400,
    "routing": 403, "needs_human": 403,
    "duplicate": 409, "transition": 409,
    "rate_limited": 429,
    "not_party": 404, "not_found": 404,
}


def _err(e: TicketError):
    """پاسخ خطای ساخت‌یافته — کد ثابت + فیلدهای اضافه (مثل duplicate_of)."""
    body = {"ok": False, "error": e.message, "code": e.code, **e.extra}
    return JSONResponse(body, status_code=_HTTP_FOR.get(e.code, 400))


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "بدنهٔ JSON نامعتبر است")
    if not isinstance(body, dict):
        raise HTTPException(400, "بدنهٔ JSON باید شیء باشد")
    return body


def _actor(request: Request, db: Session):
    """فراخواننده → (actor, human, user, project, token). ناموفق = 401."""
    user = resolve_user(request, db)
    if user is not None:
        return ("user", user.id), True, user, None, None
    row = bsvc.authenticate(db, bsvc.token_from_headers(request))
    if row is not None:
        token, project = row
        return ("project", project.id), False, None, project, token
    raise HTTPException(401, "احراز هویت لازم است — نشست پنل یا توکن پل (pst_)")


def _csrf_ok(user: User, request: Request) -> bool:
    """POST انسان واردشده — توکن CSRF از هدر (APIهای JSON فرم ندارند)."""
    return check_csrf(user.id, request.headers.get("x-csrf-token"))


def _agent_gate(token, project, request: Request, action: str, detail: str = "",
                ok: bool = True):
    """سقف نرخ + audit پل برای فراخوانی‌های عامل — best-effort."""
    if token is None:
        return
    if not bsvc.allow(token.id, "tickets"):
        bsvc.audit(token, project, action, "rate limited",
                   request.client.host if request.client else "", ok=False)
        raise HTTPException(429, "سقف نرخ پل پر شد — کمی صبر کنید")
    bsvc.audit(token, project, action, detail,
               request.client.host if request.client else "", ok=ok)


@router.get("/api/tickets")
def list_box(request: Request, box: str = "sent", limit: int = 50,
             db: Session = Depends(get_db)):
    actor, human, user, project, token = _actor(request, db)
    _agent_gate(token, project, request, "tickets.list", f"box={box}")
    try:
        rows = tsvc.list_tickets(db, actor, box, limit)
    except TicketError as e:
        return _err(e)
    return {"ok": True, "box": box,
            "tickets": [tsvc.ticket_view(db, t, human) for t in rows]}


@router.post("/api/tickets")
async def create(request: Request, db: Session = Depends(get_db)):
    actor, human, user, project, token = _actor(request, db)
    payload = await _json(request)
    if human and not _csrf_ok(user, request):
        raise HTTPException(403, "توکن CSRF نامعتبر است")
    try:
        t, dup = tsvc.create_ticket(db, actor, human, payload)
    except TicketError as e:
        _agent_gate(token, project, request, "tickets.create",
                    f"create rejected: {e.code}", ok=False)
        return _err(e)
    _agent_gate(token, project, request, "tickets.create",
                f"create #{t.id} dup={dup} to={payload.get('to', '')[:40]}")
    return {"ok": True, "duplicate": dup,
            "ticket": tsvc.ticket_view(db, t, human)}


@router.get("/api/tickets/{tid}")
def detail(request: Request, tid: int, db: Session = Depends(get_db)):
    actor, human, user, project, token = _actor(request, db)
    try:
        t = tsvc.ticket_for(db, actor, bool(user and user.is_admin), tid)
    except TicketError as e:
        return _err(e)
    _agent_gate(token, project, request, "tickets.detail", f"detail #{tid}")
    return {"ok": True, "ticket": tsvc.ticket_view(db, t, human),
            "events": tsvc.events_view(db, t, human)}


@router.post("/api/tickets/{tid}/comments")
async def comment(request: Request, tid: int, db: Session = Depends(get_db)):
    actor, human, user, project, token = _actor(request, db)
    payload = await _json(request)
    if human and not _csrf_ok(user, request):
        raise HTTPException(403, "توکن CSRF نامعتبر است")
    try:
        t = tsvc.ticket_for(db, actor, bool(user and user.is_admin), tid)
        ev = tsvc.add_comment(db, t, actor, human, str(payload.get("body") or ""))
    except TicketError as e:
        return _err(e)
    _agent_gate(token, project, request, "tickets.comment", f"comment #{tid}")
    return {"ok": True, "event_id": ev.id, "status": t.status}


@router.post("/api/tickets/{tid}/status")
async def status_change(request: Request, tid: int, db: Session = Depends(get_db)):
    actor, human, user, project, token = _actor(request, db)
    payload = await _json(request)
    if human and not _csrf_ok(user, request):
        raise HTTPException(403, "توکن CSRF نامعتبر است")
    try:
        t = tsvc.ticket_for(db, actor, bool(user and user.is_admin), tid)
        t, ev = tsvc.change_status(db, t, actor, human,
                                   bool(user and user.is_admin),
                                   str(payload.get("status") or ""),
                                   str(payload.get("evidence") or ""))
    except TicketError as e:
        return _err(e)
    _agent_gate(token, project, request, "tickets.status", f"status #{tid} → {t.status}")
    return {"ok": True, "status": t.status, "event_id": ev.id}
