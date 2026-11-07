"""SSO مالک به وب گیتا (v0.5.5) — پایان نمایش توکن گیتا به کاربر.

مالک مثل گیت‌هاب از پنل وارد مخزن گیت می‌شود: هسته با اعتبار ذخیره‌شدهٔ
حساب گیتِ کاربر (رمز فقط داخل هسته می‌ماند) یک نشست وب گیتا می‌سازد و
کوکی نشست گیتا را روی دامنهٔ پایهٔ مشترک می‌نشاند؛ مرورگر مستقیم به
git.<دامنه> می‌رود و کاربر وارد است. توکن گیتا هرگز از پنل بیرون نمی‌رود
(فقط ورکر بیلد/پل/مارکت داخلی مصرفش می‌کنند) و پوش از خط فرمان هم از
ریموت پل با توکن عامل همان پروژه انجام می‌شود.

امنیت: GET فقط صفحهٔ میانی با CSRF را رندر می‌کند و عملیات واقعی روی POST
با csrf_guard انجام می‌شود؛ کوکی فقط پس از ورود موفق سمت گیتا و فقط برای
حساب خودِ کاربر ست می‌شود (login-CSRF ندارد). کوکیِ خودِ گیتا (host-only)
در صورت وجود، مقدم بر این کوکی دامنه‌محور است — ورود معمول گیتا باز هم کار می‌کند.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from .. import domains, gitea
from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render

router = APIRouter()


def _git_ready(user) -> bool:
    return bool(user.gitea_username and user.gitea_password)


@router.get("/git/sso")
def sso_start(request: Request,
              user=Depends(csrf_guard),
              db=Depends(get_db)):
    """صفحهٔ میانی SSO — فرم خودارسال با CSRF پنل."""
    if not _git_ready(user):
        return redirect("/dashboard", "git_account_pending")
    return render("gitsso.html", ctx(request, user))


@router.post("/git/sso")
def sso_go(request: Request,
           user=Depends(csrf_guard),
           db=Depends(get_db)):
    """ورود سمت گیتا با اعتبار ذخیره‌شده + نشاندن کوکی نشست روی دامنهٔ پایه."""
    if not _git_ready(user):
        return redirect("/dashboard", "git_account_pending")
    try:
        sid = gitea.GiteaClient().form_login(user.gitea_username,
                                             user.gitea_password)
    except Exception:
        return redirect("/dashboard", "git_sso_failed")
    scheme = "https" if domains.tls() else "http"
    resp = RedirectResponse(f"{scheme}://{domains.git()}", status_code=303)
    resp.set_cookie(
        gitea.SESSION_COOKIE, sid,
        domain=f".{domains.base()}",
        httponly=True, samesite="lax",
        secure=domains.tls(), max_age=60 * 60 * 8,
    )
    return resp
