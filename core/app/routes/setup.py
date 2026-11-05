"""ویزارد راه‌اندازی — سه گام: (۱) توکن نصب ← (۲) حساب مدیر ← (۳) دامنه و HTTPS.

گام ۱ با توکن یک‌بارمصرف که فقط در خروجی install.sh چاپ شده قفل می‌شود تا
هیچ ربات اسکنری نتواند زودتر از ادمین، ویزارد را کامل کند. پس از ساخته‌شدن
مدیر، /setup برای همیشه بسته می‌شود و صفحه دامنه به‌عنوان بخش ادمین می‌ماند
(برای تغییر دامنه بعدی هم همان‌جا قابل استفاده است).
"""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import domains, traefik_ops
from ..config import settings
from ..deps import (admin_guard, ctx, get_current_user, redirect, render,
                    set_session)
from ..db import get_db
from ..models import User
from ..security import (check_setup_token, hash_password, make_gate, read_gate)
from ..validation import valid_domain

router = APIRouter()

GATE_COOKIE = "parastoo_gate"


def _admin_exists(db: Session) -> bool:
    return db.query(User).filter(User.is_admin.is_(True)).first() is not None


def _gate_ok(request: Request) -> bool:
    """اگر توکن نصب پیکربندی شده، کوکی عبور از گیت لازم است."""
    if not settings.SETUP_TOKEN:
        return True
    return read_gate(request.cookies.get(GATE_COOKIE))


# ---------- گام ۱: توکن نصب ----------

@router.get("/setup")
def setup_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    if _admin_exists(db):
        if user is not None and user.is_admin:
            return redirect("/setup/domain")
        return redirect("/login")
    if not _gate_ok(request):
        return render("setup_token.html", ctx(request, None))
    return render("setup.html", ctx(request, None, step=2))


@router.post("/setup/token")
def setup_token_submit(
    request: Request,
    token: str = Form(""),
    db: Session = Depends(get_db),
):
    if _admin_exists(db):
        return redirect("/login")
    if not check_setup_token(token):
        return redirect("/setup?m=bad_token")
    resp = redirect("/setup")
    resp.set_cookie(GATE_COOKIE, make_gate(), httponly=True,
                    samesite="lax", max_age=60 * 60 * 2)
    return resp


# ---------- گام ۲: حساب مدیر ----------

@router.post("/setup")
def setup_submit(
    request: Request,
    email: str = Form(...),
    name: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if _admin_exists(db):
        return redirect("/login")
    if not _gate_ok(request):
        return redirect("/setup?m=bad_token")
    email = email.strip().lower()
    if "@" not in email or len(password) < 6:
        return redirect("/setup?m=error")
    admin = User(
        email=email,
        name=name.strip() or "مدیر پرستو",
        password_hash=hash_password(password),
        is_admin=True,
        balance=0,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    resp = redirect("/setup/domain")
    set_session(resp, admin.id)
    return resp


# ---------- گام ۳: دامنه و HTTPS (همچنین برای تغییر بعدی) ----------

@router.get("/admin/domain")
def domain_alias(user=Depends(admin_guard)):
    return redirect("/setup/domain")


@router.get("/setup/domain")
def domain_page(
    request: Request,
    user=Depends(admin_guard),
    db: Session = Depends(get_db),
):
    base = domains.base()
    server_ip = domains.server_ip()
    records = traefik_ops.check_dns(base, server_ip)
    return render("setup_domain.html", ctx(
        request, user,
        base=base, panel=domains.panel(), git=domains.git(),
        apps=domains.apps(), tls=domains.tls(),
        acme_email=domains.acme_email(),
        is_nip=base.endswith(".nip.io"),
        server_ip=server_ip, records=records,
        pending=domains.pending_get(),
    ))


@router.get("/setup/domain/check.json")
def domain_check(
    request: Request,
    domain: str = "",
    user=Depends(admin_guard),
):
    domain = domain.strip().lower().rstrip(".")
    if not valid_domain(domain):
        return JSONResponse({"ok": False, "error": "invalid_domain"})
    records = traefik_ops.check_dns(domain, domains.server_ip())
    return JSONResponse({"ok": all(r["ok"] for r in records), "records": records})


@router.post("/setup/domain")
def domain_apply(
    request: Request,
    base_domain: str = Form(...),
    acme_email: str = Form(""),
    tls: str = Form(""),
    user=Depends(admin_guard),
    db: Session = Depends(get_db),
):
    base_domain = base_domain.strip().lower().rstrip(".")
    if not valid_domain(base_domain):
        return redirect("/setup/domain?m=domain_invalid")
    want_tls = tls == "on"
    if want_tls:
        # گواهی Let's Encrypt سقف نرخ دارد؛ قبل از درخواست، DNS باید نشسته باشد
        records = traefik_ops.check_dns(base_domain, domains.server_ip())
        if not all(r["ok"] for r in records):
            # v0.3.14 — بن‌بست نیست؛ فعال‌سازی زمان‌بندی می‌شود و ورکر هسته
            # هر ۶۰ ثانیه چک می‌کند تا پس از انتشار DNS خودش اعمال کند
            # (دستور مالک: «فرایندها را انجام بده و منتظر نباش؛ خودش درست می‌شود»).
            if "@" not in acme_email.strip():
                return redirect("/setup/domain?m=domain_invalid")
            domains.pending_set(base_domain, acme_email.strip(), True)
            return redirect("/setup/domain?m=domain_scheduled")
        if "@" not in acme_email.strip():
            return redirect("/setup/domain?m=domain_invalid")
    result = traefik_ops.apply_domain(db, base_domain,
                                      acme_email.strip(), want_tls)
    if not result["files"]:
        return redirect("/setup/domain?m=apply_failed")
    domains.pending_clear(db)  # اعمال موفق → هر انتظارِ معلق بی‌معناست
    return redirect("/setup/domain?m=domain_applied")


@router.post("/setup/domain/pending/clear")
def domain_pending_cancel(user=Depends(admin_guard)):
    """لغو دستی زمان‌بندی فعال‌سازی خودگردان (v0.3.14)."""
    domains.pending_clear()
    return redirect("/setup/domain?m=pending_cleared")
