"""تأمین‌کنندهٔ OAuth2 استاندارد پنل برای SSO به وب گیتا (v0.5.7).

جایگزین اصولی ترفند کوکی‌ریلی v0.5.5/0.5.6 (شبیه‌سازی فرم ورود گیتا +
نشاندن کوکی نشست روی دامنهٔ پایه — که به همهٔ زیردامنه‌ها از جمله اپ‌های
کاربران درز می‌کرد و به شکل فرم گیتا وابسته بود): اکنون پنل یک OAuth2
Provider واقعی (RFC 6749) است و گیتا با قابلیت رسمی خودش (Auth Source
از نوع OAuth2) به آن وصل می‌شود. هیچ شبیه‌سازی مرورگری در کار نیست؛
تنها قرارداد بیرونی، رفتار client «gitea» در goth v1.80.0 است که گیتا
1.23 پین کرده (پروفایل با ?access_token= در کوئری خوانده می‌شود).

توکن‌ها/کدها امضاشده و بدون حالت‌اند (itsdangerous/HMAC با SECRET_KEY
پنل) — بازراه‌اندازی هسته جریان‌های در حال پرواز را نمی‌شکند. کد
authorization فقط ۱۲۰ ثانیه و یک‌بارمصرف است (RFC 6749 §4.1.2).
"""
import uuid

from itsdangerous import URLSafeTimedSerializer

from .config import settings

CODE_TTL = 120          # ثانیه — عمر کد authorization
TOKEN_TTL = 3600        # ثانیه — عمر access token
REFRESH_TTL = 30 * 86400  # ثانیه — عمر refresh token (۳۰ روز)

_code_ser = URLSafeTimedSerializer(settings.SECRET_KEY, salt="parastoo-oauth-code")
_token_ser = URLSafeTimedSerializer(settings.SECRET_KEY, salt="parastoo-oauth-token")

# jti کدهای مصرف‌شده — تحمیل «یک‌بارمصرف» بدون دیتابیس. سقف حلقه‌بندی برای
# جلوگیری از رشد بی‌حد؛ پس از بازراه‌اندازی خالی می‌شود و فقط کدهای در
# حال پرواز می‌توانند یک‌بار دوباره مصرف شوند (پنجرهٔ ۱۲۰ ثانیه‌ای).
_USED_JTIS: set[str] = set()
_USED_CAP = 4096


def issue_code(uid: int, client_id: str, redirect_uri: str) -> str:
    """صدور کد authorization امضاشده، مقید به کلاینت و redirect_uri."""
    return _code_ser.dumps({
        "uid": uid, "jti": uuid.uuid4().hex,
        "cid": client_id, "uri": redirect_uri,
    })


def read_code(code: str, client_id: str, redirect_uri: str) -> int | None:
    """اعتبارسنجی و مصرف کد — uid یا None. یک‌بارمصرف همین‌جا تحمیل می‌شود."""
    try:
        data = _code_ser.loads(code, max_age=CODE_TTL)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("cid") != client_id or data.get("uri") != redirect_uri:
        return None
    jti = data.get("jti") or ""
    if not jti or jti in _USED_JTIS:
        return None
    if len(_USED_JTIS) >= _USED_CAP:
        _USED_JTIS.clear()
    _USED_JTIS.add(jti)
    return data.get("uid")


def issue_tokens(uid: int) -> tuple[str, str, int]:
    """صدور جفت access/refresh token امضاشده — خروجی (at, rt, expires_in)."""
    at = _token_ser.dumps({"uid": uid, "typ": "at"})
    rt = _token_ser.dumps({"uid": uid, "typ": "rt"})
    return at, rt, TOKEN_TTL


def read_token(token: str, typ: str = "at") -> int | None:
    """اعتبارسنجی توکن امضاشده — uid یا None."""
    if not token:
        return None
    try:
        data = _token_ser.loads(token, max_age=REFRESH_TTL if typ == "rt" else TOKEN_TTL)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("typ") != typ:
        return None
    return data.get("uid")
