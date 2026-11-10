"""مسیرهای تیکتینگ عضو‌به‌عضو (v0.5.17 → Cycle 3) — /api/tickets* + صفحات پنل.

همهٔ قواعد در services/tickets.py است؛ اینجا فقط احراز هویت، CSRF،
نگاشت خطا به HTTP/فلش و JSON. سه نوع فراخواننده:
- انسان (API): کوکی نشست پنل (کاربر/ادمین) + CSRF در POST (هدر X-CSRF-Token)
- انسان (UI): فرم‌های پنل با فیلد csrf (گارد csrf_guard) — /tickets*
- عامل پروژه: توکن پل `pst_` (Bearer/Basic — همان قرارداد پل) + باکت نرخ + audit
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, RedirectResponse

from ..db import get_db
from ..deps import csrf_guard, ctx, get_current_user, render, resolve_user
from ..models import User
from ..security import check_csrf
from ..services import bridge as bsvc
from ..services import tickets as tsvc
from ..services.tickets import TicketError
from ..tpl import FLASHES

router = APIRouter()

# نگاشت کد خطا → وضعیت HTTP (IDOR-safe: نقض دسترسی همیشه 404)
_HTTP_FOR = {
    "bad_target": 400, "bad_box": 400, "schema": 400,
    "routing": 403, "needs_human": 403,
    "duplicate": 409, "transition": 409,
    "rate_limited": 429,
    "not_party": 404, "not_found": 404,
}

# نگاشت کد خطا → کلید فلش پنل (صفحات)
_FLASH_FOR = {
    "bad_target": "ticket_bad_target", "schema": "ticket_schema",
    "routing": "ticket_routing", "duplicate": "ticket_duplicate",
    "rate_limited": "ticket_rate", "transition": "ticket_transition",
    "not_party": "ticket_denied", "needs_human": "ticket_needs_human",
    "not_found": "not_found", "bad_box": "error",
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
    actor, human, _user, _project, token = _actor(request, db)
    _agent_gate(token, _project, request, "tickets.list", f"box={box}")
    try:
        rows = tsvc.list_tickets(db, actor, box, limit)
    except TicketError as e:
        return _err(e)
    return {"ok": True, "box": box,
            "tickets": [tsvc.ticket_view(db, t, human) for t in rows]}


@router.get("/api/tickets/summary")
def summary(request: Request, db: Session = Depends(get_db)):
    """شمار صندوق باز فراخواننده — همان تک‌منبعِ نشان UI و قلاب پل.
    قبل از مسیر {tid} ثبت می‌شود تا «summary» به‌عنوان شناسه تفسیر نشود."""
    actor, _human, _user, _project, token = _actor(request, db)
    _agent_gate(token, _project, request, "tickets.summary", "summary")
    return {"ok": True, **tsvc.open_counts(db, actor)}


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


# ---------- صفحات پنل (Cycle 3) — فرم‌محور، CSRF با فیلد فرم ----------

def _box_rows(db, actor: tuple, box: str):
    """ردیف‌های صندوق برای رندر — با برچسب‌های تک‌منبع سرویس."""
    return [tsvc.ticket_view(db, t, True) for t in tsvc.list_tickets(db, actor, box)]


@router.get("/tickets")
def tickets_page(request: Request, box: str = "inbox",
                 user: User | None = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """صندوق تیکت‌ها (ورودی/ارسالی) + فرم ساخت — ناوبری با نشان شمار باز."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    actor = ("user", user.id)
    if box not in ("inbox", "sent"):
        box = "inbox"
    return render("tickets.html", ctx(request, user, box=box,
                                      tickets=_box_rows(db, actor, box),
                                      counts=tsvc.open_counts(db, actor)))


@router.get("/tickets/{tid}")
def ticket_page(request: Request, tid: int,
                user: User | None = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """جزئیات تیکت + دفتر رخدادها + کامنت + گذارهای مجاز این نقش."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    actor = ("user", user.id)
    try:
        t = tsvc.ticket_for(db, actor, user.is_admin, tid)
    except TicketError:
        # غریبه = بدون لو دادن وجود (همان سیاست IDOR-safe API)
        return RedirectResponse("/tickets?m=not_found", status_code=303)
    return render("ticket_detail.html", ctx(request, user,
                   t=tsvc.ticket_view(db, t, True),
                   events=tsvc.events_view(db, t, True),
                   allowed=sorted(tsvc.allowed_moves(t, actor, user.is_admin)),
                   counts=tsvc.open_counts(db, actor)))


@router.post("/tickets/new")
async def ticket_new(request: Request, user: User = Depends(csrf_guard),
                     db: Session = Depends(get_db)):
    """فرم ساخت تیکت — همان اسکیمای سخت و ماتریس مسیریابیِ API؛
    خطا = رندر دوبارهٔ فرم با حفظ مقادیر (تلاش کاربر دور ریخته نمی‌شود)."""
    form = await request.form()
    actor = ("user", user.id)
    payload = {
        "to": form.get("to"), "kind": form.get("kind"),
        "severity": form.get("severity"), "title": form.get("title"),
        "observed": form.get("observed"), "expected": form.get("expected"),
        "body": form.get("body"),
        "evidence": [ln.strip() for ln in str(form.get("evidence") or "").splitlines()
                     if ln.strip()],
    }
    try:
        t, _dup = tsvc.create_ticket(db, actor, True, payload)
    except TicketError as e:
        return render("tickets.html", ctx(request, user, box="inbox",
                       tickets=_box_rows(db, actor, "inbox"),
                       counts=tsvc.open_counts(db, actor), prev=payload,
                       form_error=FLASHES.get(_FLASH_FOR.get(e.code, "error"),
                                              FLASHES["error"])))
    return RedirectResponse(f"/tickets/{t.id}?m=ticket_created", status_code=303)


@router.post("/tickets/{tid}/comment")
async def ticket_comment(tid: int, request: Request,
                         user: User = Depends(csrf_guard),
                         db: Session = Depends(get_db)):
    """کامنت از فرم پنل — همان قواعد add_comment (تیکت حساس = فقط انسان)."""
    form = await request.form()
    actor = ("user", user.id)
    try:
        t = tsvc.ticket_for(db, actor, user.is_admin, tid)
        tsvc.add_comment(db, t, actor, True, str(form.get("body") or ""))
    except TicketError as e:
        return RedirectResponse(f"/tickets/{tid}?m={_FLASH_FOR.get(e.code, 'error')}",
                                status_code=303)
    return RedirectResponse(f"/tickets/{tid}?m=ticket_commented", status_code=303)


@router.post("/tickets/{tid}/status")
async def ticket_status(tid: int, request: Request,
                        user: User = Depends(csrf_guard),
                        db: Session = Depends(get_db)):
    """گذار وضعیت از فرم پنل — نقش‌محور (allowed_moves تک‌منبع)؛
    resolved سندِ کامیت می‌خواهد، بازگشایی دوم بن‌بست می‌شود."""
    form = await request.form()
    actor = ("user", user.id)
    try:
        t = tsvc.ticket_for(db, actor, user.is_admin, tid)
        tsvc.change_status(db, t, actor, True, user.is_admin,
                           str(form.get("status") or ""),
                           str(form.get("evidence") or ""))
    except TicketError as e:
        return RedirectResponse(f"/tickets/{tid}?m={_FLASH_FOR.get(e.code, 'error')}",
                                status_code=303)
    return RedirectResponse(f"/tickets/{tid}?m=ticket_status_changed", status_code=303)
