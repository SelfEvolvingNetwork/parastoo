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
    return resp


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
