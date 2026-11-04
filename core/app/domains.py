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


# ---------- IP عمومی واقعی (منبع حقیقت زنده) ----------

_IP_LIVE_CACHE = ""
_IP_LIVE_AT = 0.0


def _nipio_ip(domain: str) -> str:
    """IP کدشده در نام دامنهٔ nip.io — مثل panel.193.151.142.128.nip.io → 193.151.142.128."""
    if not domain or not domain.endswith(".nip.io"):
        return ""
    labels = domain[:-len(".nip.io")].split(".")
    if len(labels) < 4:
        return ""
    ip = ".".join(labels[-4:])
    if all(p.isdigit() and 0 <= int(p) <= 255 for p in ip.split(".")):
        return ip
    return ""


def _resolve(host: str) -> str:
    """رزولوشن A یک میزبان — بدون استثنا."""
    if not host:
        return ""
    try:
        import socket
        infos = socket.getaddrinfo(host, 80, family=socket.AF_INET)
        return infos[0][4][0] if infos else ""
    except Exception:
        return ""


def _egress_ip() -> str:
    """IP از سرویس‌های egress (منبع ضعیف‌تر — ممکن است IP خروجی NAT باشد نه ورودی)."""
    if _IP_CACHE:
        return _IP_CACHE
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            import httpx
            r = httpx.get(url, timeout=5)
            if r.status_code == 200 and r.text.strip():
                _IP_CACHE = r.text.strip()
                return _IP_CACHE
        except Exception:
            continue
    return ""


def server_ip(db=None) -> str:
    """IP عمومیِ واقعی سرور — برای چک DNS (قبل از فعال‌سازی TLS).

    منبع حقیقت زنده، به ترتیب:
      ۱) دامنهٔ فعال پنل اگر nip.io باشد، IP را داخل خودِ نام دارد (بدون DNS)؛
      ۲) وگرنه رزولوشن دامنهٔ فعال (ترافیک از همان‌جا می‌گذرد، پس همان IP سرور است)؛
      ۳) سپس PUBLIC_IP env؛
      ۴) وگرنه تشخیص egress. نتیجه ۶۰ ثانیه کش می‌شود (تا تغییر دامنه بلافاصله دیده شود).

    انگیزه: نصب اول با IP خروجی NAT اجرا شده بود و PUBLIC_IP همان در .env ماند،
    در حالی که IP عمومی واقعی چیز دیگری است؛ چک DNS ویزارد با مقایسهٔ رکوردها با
    همان IP کهنه همیشه شکست می‌خورد و فعال‌سازی دامنهٔ واقعی و TLS ناممکن می‌شد.
    """
    global _IP_LIVE_CACHE, _IP_LIVE_AT, _IP_CACHE
    now = time.time()
    if _IP_LIVE_CACHE and now - _IP_LIVE_AT < 60.0:
        return _IP_LIVE_CACHE
    ip = _nipio_ip(panel(db))
    if not ip:
        resolved = _resolve(panel(db)) or _resolve(git(db))
        if resolved:
            ip = resolved
        elif settings.PUBLIC_IP:
            ip = settings.PUBLIC_IP
        else:
            ip = _egress_ip()
    if ip:
        _IP_LIVE_CACHE, _IP_LIVE_AT = ip, now
        _IP_CACHE = ip
    return ip
