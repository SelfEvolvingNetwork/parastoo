"""وابستگی‌های مشترک: کاربر جاری، محافظ CSRF، ساخت context قالب‌ها."""
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import check_csrf, csrf_token, read_session
from .tpl import FLASHES, templates

COOKIE_NAME = "parastoo_session"


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = read_session(token)
    if uid is None:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active:
        return None
    return user


def require_login(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=303, headers={"Location": "/?m=denied"})
    return user


async def csrf_guard(
    request: Request,
    user: User = Depends(require_login),
) -> User:
    """محافظ CSRF برای همه POSTهای کاربر واردشده."""
    if request.method == "POST":
        form = await request.form()
        if not check_csrf(user.id, form.get("csrf")):
            raise HTTPException(status_code=303, headers={"Location": "/?m=csrf"})
    return user


async def admin_guard(
    request: Request,
    user: User = Depends(require_admin),
) -> User:
    """ادمین بودن + CSRF — برای همه مسیرهای /admin."""
    if request.method == "POST":
        form = await request.form()
        if not check_csrf(user.id, form.get("csrf")):
            raise HTTPException(status_code=303, headers={"Location": "/?m=csrf"})
    return user


def ctx(request: Request, user: User | None, **extra: Any) -> dict:
    """context پایه همه قالب‌ها."""
    base = {
        "request": request,
        "user": user,
        "m": request.query_params.get("m"),
        "flash": FLASHES.get(request.query_params.get("m", ""), ""),
        "path": request.url.path,
    }
    if user:
        base["csrf"] = csrf_token(user.id)
    base.update(extra)
    return base


def render(name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(name=name, context=context, status_code=status_code)


def redirect(url: str, m: str | None = None):
    from fastapi.responses import RedirectResponse
    if m:
        sep = "&" if "?" in url else "?"
        return RedirectResponse(f"{url}{sep}m={m}", status_code=303)
    return RedirectResponse(url, status_code=303)


def set_session(response, uid: int) -> None:
    from .security import make_session
    response.set_cookie(
        COOKIE_NAME, make_session(uid),
        httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14,
    )


def clear_session(response) -> None:
    response.delete_cookie(COOKIE_NAME)
