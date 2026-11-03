"""امنیت: هش رمز (scrypt استانداردlib)، کوکی نشست امضاشده، CSRF."""
import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_SESSION_SALT = "parastoo-session"
_SESSION_MAX_AGE = 60 * 60 * 24 * 14  # دو هفته


# ---------- رمز عبور ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, h_hex = stored.split("$")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


# ---------- نشست ----------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SESSION_SALT)


def make_session(uid: int) -> str:
    return _serializer().dumps({"uid": uid})


def read_session(token: str) -> int | None:
    try:
        data = _serializer().loads(token, max_age=_SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


# ---------- CSRF ----------

def csrf_token(uid: int) -> str:
    msg = f"csrf:{uid}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def check_csrf(uid: int, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(csrf_token(uid), token)


# ---------- گیت ویزارد نصب (توکن یک‌بارمصرف) ----------

_GATE_SALT = "parastoo-setup-gate"
_GATE_MAX_AGE = 60 * 60 * 2  # کوکی عبور از گیت: دو ساعت


def check_setup_token(submitted: str | None) -> bool:
    """مقایسه امن توکن واردشده با توکن چاپ‌شده در خروجی install.sh."""
    if not submitted or not settings.SETUP_TOKEN:
        return False
    return hmac.compare_digest(submitted.strip(), settings.SETUP_TOKEN)


def make_gate() -> str:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_GATE_SALT).dumps("ok")


def read_gate(token: str | None) -> bool:
    if not token:
        return False
    try:
        return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_GATE_SALT).loads(
            token, max_age=_GATE_MAX_AGE) == "ok"
    except (BadSignature, SignatureExpired):
        return False


# ---------- ابزار ----------

def gen_secret(n: int = 32) -> str:
    return secrets.token_hex(n)


def gen_password(n: int = 20) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


SLUG_OK = "abcdefghijklmnopqrstuvwxyz0123456789-"


def slugify(text: str, max_len: int = 40) -> str:
    """تبدیل متن به شناسه امن لاتین (برای slug)."""
    out = []
    for ch in text.strip().lower():
        if ch in SLUG_OK:
            out.append(ch)
        elif ch in (" ", "_", "."):
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:max_len]
