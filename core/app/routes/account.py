"""صفحهٔ حساب کاربری + تغییر رمز (ورود/ثبت‌نام در auth.py جدا شده‌اند)."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..deps import ctx, csrf_guard, redirect, render
from ..db import get_db
from ..security import hash_password, verify_password

router = APIRouter()


@router.get("/account")
def account_page(request: Request, user=Depends(csrf_guard)):
    return render("account.html", ctx(request, user))


@router.post("/account/password")
def account_change_password(
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
    user=Depends(csrf_guard),
    db: Session = Depends(get_db),
):
    """تغییر رمز پنل: رمز فعلی الزامی است؛ کوتاهی/ناهمخوانی با پیام مشخص رد می‌شود.

    توجه: رمز حساب گیتِ خودکارِ کاربر جداگانه است و با این تغییر دست نمی‌خورد
    (دسترسی گیت با توکن انجام می‌شود، نه رمز).
    """
    if not verify_password(current_password, user.password_hash):
        return redirect("/account?m=bad_current")
    if len(new_password) < 6:
        return redirect("/account?m=pwd_short")
    if new_password != new_password2:
        return redirect("/account?m=pwd_mismatch")
    user.password_hash = hash_password(new_password)
    db.commit()
    return redirect("/account?m=password_changed")
