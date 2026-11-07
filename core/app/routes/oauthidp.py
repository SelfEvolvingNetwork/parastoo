"""اندپوینت‌های OAuth2 Provider پنل (v0.5.7) — SSO اصولی به وب گیتا.

مسیر استاندارد: مرورگر کاربر از گیتا به /oauth/authorize پنل می‌آید
(نشست پنل هویت را ثابت می‌کند)، کد امضاشده به callback گیتا برمی‌گردد
و سرور گیتا (نه مرورگر) با POST /oauth/token کد را با اعتبار کلاینت
تبادل می‌کند و پروفایل را از /oauth/userinfo می‌خواند. هویتِ برگشتی با
[oauth2_client] ACCOUNT_LINKING=auto گیتا به حساب گیتای «همان کاربر»
(هم‌ایمیل) پیوند می‌خورد — همان حسابی که موتور از قبل برایش ساخته است.
"""
import base64
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import domains, gitea, oauthidp
from ..db import get_db
from ..deps import get_current_user
from ..models import User

router = APIRouter()


def _scheme() -> str:
    return "https" if domains.tls() else "http"


def _redirect_uri() -> str:
    """callback دقیق گیتا — منبع SSO با همین رشتهٔ دقیق ثبت می‌شود."""
    return f"{_scheme()}://{domains.git()}/user/oauth2/{gitea.SSO_SOURCE_NAME}/callback"


def _oauth_error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


@router.get("/oauth/authorize")
def oauth_authorize(request: Request,
                    response_type: str = "",
                    client_id: str = "",
                    redirect_uri: str = "",
                    state: str = "",
                    user: User | None = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """صدور کد برای کاربر واردشدهٔ پنل — فقط کلاینت ثبت‌شده و callback دقیق."""
    if user is None:
        # نشست پنل نیست → اول ورود پنل، بعد ادامهٔ همان جریان
        # (next فقط مسیر محلی می‌پذیرد — گارد open-redirect در auth.py)
        nxt = request.url.path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(
            f"/login?m=login_required&next={quote(nxt, safe='')}", status_code=303)
    cid, _ = gitea.sso_client_creds(db)
    if (response_type != "code" or client_id != cid
            or redirect_uri != _redirect_uri()):
        return _oauth_error("invalid_request")
    code = oauthidp.issue_code(user.id, cid, redirect_uri)
    sep = "&" if "?" in redirect_uri else "?"
    # state فقط به‌صورت بازکدگذاری‌شده برمی‌گردد تا تزریق پارامتر ممکن نباشد
    return RedirectResponse(
        f"{redirect_uri}{sep}code={quote(code, safe='')}&state={quote(state, safe='')}",
        status_code=302)


@router.post("/oauth/token")
def oauth_token(request: Request,
                grant_type: str = Form(""),
                code: str = Form(""),
                redirect_uri: str = Form(""),
                client_id: str = Form(""),
                client_secret: str = Form(""),
                refresh_token: str = Form(""),
                db: Session = Depends(get_db)):
    """تبادل کد/refresh — این endpoint فقط سرور گیتا مصرفش می‌کند."""
    cid, csec = gitea.sso_client_creds(db)
    # احراز کلاینت: x/oauth2 ابتدا اعتبار را در بدنهٔ فرم می‌فرستد و در
    # فالبک با HTTP Basic — هر دو پذیرفته می‌شود (RFC 6749 §2.3.1)
    if not client_id or not client_secret:
        h = request.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                buser, bpass = base64.b64decode(h[6:]).decode().split(":", 1)
            except Exception:
                return _oauth_error("invalid_client", 401)
            client_id = client_id or buser
            client_secret = client_secret or bpass
    if not (client_id and client_secret
            and secrets.compare_digest(client_id, cid)
            and secrets.compare_digest(client_secret, csec)):
        return _oauth_error("invalid_client", 401)

    if grant_type == "authorization_code":
        if redirect_uri != _redirect_uri():
            return _oauth_error("invalid_grant")
        uid = oauthidp.read_code(code, cid, redirect_uri)
    elif grant_type == "refresh_token":
        uid = oauthidp.read_token(refresh_token, typ="rt")
    else:
        return _oauth_error("unsupported_grant_type")
    if uid is None:
        return _oauth_error("invalid_grant")

    at, rt, exp = oauthidp.issue_tokens(uid)
    return {"access_token": at, "token_type": "bearer",
            "expires_in": exp, "refresh_token": rt}


@router.get("/oauth/userinfo")
def oauth_userinfo(request: Request,
                   access_token: str = "",
                   db: Session = Depends(get_db)):
    """پروفایل هم‌شکل /api/v1/user گیتا — قرارداد client «gitea» در goth
    (توکن در کوئری می‌آید؛ Bearer هم پذیرفته می‌شود)."""
    token = access_token
    if not token:
        h = request.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            token = h[7:].strip()
    uid = oauthidp.read_token(token) if token else None
    if uid is None:
        return _oauth_error("invalid_token", 401)
    user = db.get(User, uid)
    if user is None or not user.is_active:
        return _oauth_error("invalid_token", 401)
    uname = user.gitea_username or user.email.split("@")[0]
    return {
        "id": user.id,
        "sub": str(user.id),
        # قرارداد goth (client «gitea»): هویت از login/email/full_name/avatar_url
        # خوانده می‌شود. کلیدهای OIDC زیر برای نگاشت [oauth2_client] USERNAME
        # گیتا لازم‌اند — گیتا 1.23 برای preferred_username فال‌بک به login ندارد
        # (میدان 2026-11-07: بدون این کلید، auto-reg با «missing fields» می‌ماند).
        "login": uname,
        "preferred_username": uname,
        "nickname": uname,
        "email": user.email,
        "full_name": user.name or "",
        "avatar_url": "",
    }
