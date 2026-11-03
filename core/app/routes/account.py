"""ثبت‌نام، ورود، خروج."""
import re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import gitea
from ..config import settings
from ..deps import (clear_session, ctx, get_current_user, redirect, render,
                    set_session)
from ..db import get_db
from ..models import User
from ..security import hash_password, verify_password

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    if not EMAIL_RE.match(email) or len(password) < 6:
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


@router.get("/login")
def login_page(
    request: Request,
    user: User | None = Depends(get_current_user),
):
    if user:
        return redirect("/")
    return render("login.html", ctx(request, None))


@router.post("/login")
def login_submit(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return redirect("/login?m=bad_login")
    resp = redirect("/")
    set_session(resp, user.id)
    return resp


@router.get("/logout")
def logout():
    resp = redirect("/login?m=logged_out")
    clear_session(resp)
    return resp
