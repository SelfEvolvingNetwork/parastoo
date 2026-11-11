"""احراز هویت: ثبت‌نام، ورود، خروج + ضد brute-force.

تا v0.2.24 این منطق داخل routes/account.py بود و «مدیریت حساب» (تغییر رمز)
با «ورود/خروج» یک فایل داشتند؛ از v0.3.0 حساب کاربری در account.py جدا است.
"""
import time

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import gitea
from ..config import settings
from ..deps import (clear_session, ctx, get_current_user, redirect, render,
                    set_session)
from ..db import get_db
from ..models import User
from ..security import hash_password, verify_password
from ..services import email as mailer
from ..services import password_reset as prs
from ..validation import valid_email

router = APIRouter()


# ---------- محدودسازی تلاش ورود (ضد brute-force) ----------
# حافظهٔ درون‌پردازشی: کلید «IP|ایمیل» → زمان‌های شکست اخیر. پس از MAX_FAILS شکست
# در پنجرهٔ FAIL_WINDOW، همان کلید به مدت LOCK_SECONDS قفل می‌شود (با همان
# پیام bad_login — بدون لو دادن وجود قفل). ورود موفق شمارنده را صفر می‌کند.
_LOGIN_FAILS: dict[str, list[float]] = {}
_LOCKS: dict[str, float] = {}
FAIL_WINDOW = 600.0   # پنجرهٔ ۱۰ دقیقه‌ای
MAX_FAILS = 8         # حداکثر شکست مجاز در پنجره
LOCK_SECONDS = 300.0  # قفل ۵ دقیقه‌ای پس از تجاوز


def _client_ip(request: Request) -> str:
    # پشت ترافیک: آخرین X-Forwarded-For واقعی‌ترین مشتری است؛ وگرنه socket
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def login_locked(key: str) -> bool:
    until = _LOCKS.get(key, 0)
    if until and time.time() < until:
        return True
    if until:
        del _LOCKS[key]
    return False


def login_record_fail(key: str) -> None:
    now = time.time()
    fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < FAIL_WINDOW]
    fails.append(now)
    _LOGIN_FAILS[key] = fails
    if len(fails) >= MAX_FAILS:
        _LOCKS[key] = now + LOCK_SECONDS
        _LOGIN_FAILS[key] = []


def login_reset(key: str) -> None:
    _LOGIN_FAILS.pop(key, None)
    _LOCKS.pop(key, None)


# ---------- ثبت‌نام ----------

@router.get("/register")
def register_page(
    request: Request,
    user: User | None = Depends(get_current_user),
):
    if user:
        return redirect("/")
    if not settings.REGISTER_OPEN:
        return redirect("/login?m=reg_closed")
    return render("register.html", ctx(request, None))


@router.post("/register")
def register_submit(
    email: str = Form(...),
    name: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not settings.REGISTER_OPEN:
        return redirect("/login?m=reg_closed")
    email = email.strip().lower()
    if not valid_email(email) or len(password) < 6:
        return redirect("/register?m=error")
    if db.query(User).filter(User.email == email).first():
        return redirect("/register?m=error")
    user = User(
        email=email,
        name=name.strip() or email.split("@")[0],
        password_hash=hash_password(password),
        balance=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # ساخت حساب گیت؛ اگر گیتا هنوز بالا نیست، ورکر پس‌زمینه بعداً انجام می‌دهد
    try:
        gitea.provision_user_git(user)
        db.commit()
    except Exception:
        db.rollback()
    resp = redirect("/?m=reg_ok")
    set_session(resp, user.id)
    # نامهٔ خوش‌آمد — best-effort؛ شکست ارسال هرگز ثبت‌نام را نمی‌شکند
    mailer.send_email(
        user.email, "به پرستو خوش آمدید",
        "سلام،\n\nحساب شما در پرستو ساخته شد.\n"
        f"آدرس ورود به پنل: {_panel_url()}\n"
        f"ایمیل: {user.email}\n"
        "اگر شما این حساب را نساخته‌اید، این نامه را نادیده بگیرید.\n\n— پرستو")
    return resp


# ---------- یادآوری رمز عبور (ایمیل بازنشانی — v0.5.23) ----------

def _panel_url() -> str:
    scheme = "https" if settings.TLS_ENABLED else "http"
    return f"{scheme}://{settings.PANEL_DOMAIN}"


def _reset_link(token: str) -> str:
    return f"{_panel_url()}/reset-password?token={token}"


@router.get("/forgot-password")
def forgot_password_page(
    request: Request,
    user: User | None = Depends(get_current_user),
):
    if user:
        return redirect("/")
    return render("forgot_password.html", ctx(request, None))


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    db: Session = Depends(get_db),
):
    """پاسخ همیشه یکسان است — وجود/نبود حساب و محدودیت نرخ لو نمی‌رود.
    رمز هرگز ایمیل نمی‌شود؛ فقط لینک بازنشانی یک‌بارمصرف (وصیت ۹)."""
    email = (email or "").strip().lower()
    if valid_email(email):
        user = (db.query(User)
                .filter(User.email == email, User.is_active == True)  # noqa: E712
                .first())
        if user:
            ip = _client_ip(request)
            raw = prs.issue(db, user, ip)
            if raw:
                body = (
                    "سلام،\n\n"
                    "درخواست بازنشانی رمز عبور حساب پرستو شما ثبت شد.\n"
                    "لینک بازنشانی (۶۰ دقیقه اعتبار دارد):\n"
                    + _reset_link(raw) + "\n\n"
                    "اگر شما این درخواست را نداده‌اید، این نامه را نادیده "
                    "بگیرید — رمز شما تغییری نمی‌کند.\n\n— پرستو"
                )
                mailer.send_email(user.email, "بازنشانی رمز عبور پرستو", body)
    return redirect("/forgot-password", m="forgot_sent")


@router.get("/reset-password")
def reset_password_page(
    request: Request,
    token: str = "",
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user:
        return redirect("/")
    if not token or not prs.peek(db, token):
        return redirect("/forgot-password", m="reset_bad_token")
    return render("reset_password.html", ctx(request, None, token=token))


@router.post("/reset-password")
def reset_password_submit(
    token: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    db: Session = Depends(get_db),
):
    """اول اعتبارسنجی رمز، بعد مصرف توکن — غلط تایپی نباید لینک را بسوزاند."""
    if len(password) < 6:
        return redirect(f"/reset-password?token={token}", m="pwd_short")
    if password != password2:
        return redirect(f"/reset-password?token={token}", m="pwd_mismatch")
    user = prs.consume(db, token)
    if user is None:
        return redirect("/forgot-password", m="reset_bad_token")
    user.password_hash = hash_password(password)
    db.commit()
    mailer.send_email(user.email, "رمز عبور پرستو تغییر کرد",
                      "سلام،\n\nرمز عبور حساب پرستو شما با لینک بازنشانی "
                      "تغییر کرد.\nاگر شما این کار را انجام نداده‌اید، "
                      "فوراً با مدیر تماس بگیرید.\n\n— پرستو")
    return redirect("/login", m="password_reset_ok")


# ---------- ورود / خروج ----------

def _safe_next(nxt: str | None) -> str | None:
    """فقط مسیر محلی — گارد open-redirect برای برگشت از جریان‌هایی مثل
    /oauth/authorize (SSO گیتا). «//host» و schemeها رد می‌شوند."""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") \
            or "://" in nxt or "\\" in nxt:
        return None
    return nxt


@router.get("/login")
def login_page(
    request: Request,
    user: User | None = Depends(get_current_user),
    nxt: str = "",
):
    if user:
        return redirect(_safe_next(nxt) or "/")
    return render("login.html", ctx(request, None, next=_safe_next(nxt)))


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    key = f"{_client_ip(request)}|{email}"
    if login_locked(key):
        # پیام همان bad_login می‌ماند تا وجود قفل اطلاعات اضافه لو ندهد
        return redirect("/login?m=bad_login")
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        login_record_fail(key)
        return redirect("/login?m=bad_login")
    login_reset(key)
    resp = redirect(_safe_next(next) or "/")
    set_session(resp, user.id)
    return resp


@router.get("/logout")
def logout():
    resp = redirect("/login?m=logged_out")
    clear_session(resp)
    return resp
