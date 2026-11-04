"""دامنه‌های فعال پرستو — لایه پویا روی تنظیمات env.

ویزارد دامنه (پنل وب) مقادیر جدید را در جدول Setting ذخیره می‌کند و
همه‌جا که دامنه لازم است از این ماژول خوانده می‌شود؛ به این ترتیب تغییر
دامنه بدون ری‌استارت هسته و در چند ثانیه اعمال می‌شود.
کلیدهای جدول Setting با پیشوند «domain_» ذخیره می‌شوند.
"""
import time

from .config import settings
from .db import SessionLocal
from .models import Setting

_DOMAIN_KEYS = ("domain_base", "domain_panel", "domain_git",
                "domain_apps", "domain_tls", "domain_acme_email")

_CACHE: dict[str, str] = {}
_CACHE_AT = 0.0
_CACHE_TTL = 5.0  # ثانیه

_IP_CACHE = ""


def _all() -> dict[str, str]:
    global _CACHE, _CACHE_AT
    now = time.time()
    if _CACHE and now - _CACHE_AT < _CACHE_TTL:
        return _CACHE
    db = SessionLocal()
    try:
        rows = db.query(Setting).filter(Setting.key.in_(_DOMAIN_KEYS)).all()
        _CACHE = {r.key: r.value for r in rows}
        _CACHE_AT = now
    finally:
        db.close()
    return _CACHE


def _invalidate() -> None:
    global _CACHE_AT
    _CACHE = {}
    _CACHE_AT = 0.0


def set_many(values: dict[str, str], db=None) -> None:
    """ذخیره مقادیر دامنه در جدول Setting (با نشست بیرونی یا خودکار)."""
    own = db is None
    s = db or SessionLocal()
    try:
        for k, v in values.items():
            row = s.get(Setting, k)
            if row is None:
                s.add(Setting(key=k, value=v))
            else:
                row.value = v
        s.commit()
    finally:
        if own:
            s.close()
    _invalidate()


def base(db=None) -> str:
    return _all().get("domain_base") or settings.BASE_DOMAIN


def panel(db=None) -> str:
    return _all().get("domain_panel") or settings.PANEL_DOMAIN


def git(db=None) -> str:
    return _all().get("domain_git") or settings.GIT_DOMAIN


def apps(db=None) -> str:
    return _all().get("domain_apps") or settings.APPS_DOMAIN


def acme_email(db=None) -> str:
    return _all().get("domain_acme_email") or ""


def tls(db=None) -> bool:
    v = _all().get("domain_tls")
    if v == "":
        return settings.TLS_ENABLED  # هنوز ویزارد اجرا نشده → مقدار env نصب
    return v == "1"


def server_ip() -> str:
    """IP عمومی سرور — از env نصب، وگرنه تشخیص خودکار (یک‌بار کش می‌شود)."""
    global _IP_CACHE
    if settings.PUBLIC_IP:
        return settings.PUBLIC_IP
    if _IP_CACHE:
        return _IP_CACHE
    try:
        import httpx
        r = httpx.get("https://api.ipify.org", timeout=5)
        _IP_CACHE = r.text.strip()
    except Exception:
        pass
    return _IP_CACHE
