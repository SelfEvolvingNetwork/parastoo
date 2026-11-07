"""SSO مالک به وب گیتا (v0.5.7) — مسیر استاندارد OAuth2.

تاریخچه: v0.5.5/0.5.6 نشست گیتا را با شبیه‌سازی فرم ورود (استخراج CSRF +
POST اعتبار ذخیره‌شده) و نشاندن کوکی نشست روی دامنهٔ پایه می‌ساخت — ترفندی
شکننده که کوکی گیتا را به همهٔ زیردامنه‌ها از جمله اپ‌های کاربران درز می‌داد
و هر تغییر شکل فرم گیتا را می‌شکست. اکنون (فرمان مالک: «اصولی و بدون ترفند»)
پنل یک OAuth2 Provider استاندارد است و گیتا با قابلیت رسمی خودش (Auth
Source از نوع OAuth2 با custom URLs — خودکار با ensure_sso_source ساخته
می‌شود) به آن متصل است: این endpoint فقط مرورگر را به جریان رسمی
/user/oauth2/parastoo گیتا می‌فرستد و هویت پنل با ACCOUNT_LINKING=auto
به حساب گیتای همان کاربر (هم‌ایمیل) پیوند می‌خورد. هیچ کوکی‌ای اینجا
دستکاری نمی‌شود و توکن گیتا هرگز از پنل بیرون نمی‌رود.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import domains, gitea
from ..db import get_db
from ..deps import redirect, require_login
from ..models import User

router = APIRouter()


@router.get("/git/sso")
def sso_start(request: Request,
              user: User = Depends(require_login),
              db: Session = Depends(get_db)):
    """ورود به جریان رسمی OAuth2 گیتا (کلاینت «parastoo» روی گیتا)."""
    if not user.gitea_username:
        # حساب گیت هنوز ساخته نشده (کاربر تازه قبل از اولین پروژه) —
        # همان مسیر idempotent ورکرها؛ بدون آن، لینک‌کردن هویت ناقص می‌ماند
        try:
            gitea.provision_user_git(user)
            db.commit()
        except Exception:
            db.rollback()
            return redirect("/dashboard", "git_account_pending")
    scheme = "https" if domains.tls() else "http"
    return RedirectResponse(
        f"{scheme}://{domains.git()}/user/oauth2/{gitea.SSO_SOURCE_NAME}",
        status_code=302)
