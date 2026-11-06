"""اعتبارسنجی مشترک — یک لایه، یک حقیقت.

تا v0.2.24 سه الگوی اعتبارسنجی در سه ماژول مسیر پراکنده بود
(SLUG_RE در projects.py که marketplace از آن import می‌کرد، EMAIL_RE در
account.py، DOMAIN_RE در setup.py)؛ هر اصلاح قاعده باید سه جا تکرار می‌شد.
حالا همهٔ فرم‌ها/الگوها اینجاست و مسیرها فقط خطای متناظر را برمی‌گردانند.
"""
import re

from .config import settings

# شناسهٔ پروژه/سایت: لاتین کوچک، عدد، خط‌تیره — ۳ تا ۴۰ کاراکتر
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")

# ایمیل با ساده‌ترین قاعده (نام@دامنه.پسوند) — قواعد سختگیرانهٔ RFC عملاً بی‌فایده‌اند
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# دامنه پایه: حداقل دو برچسب، همه لاتین کوچک/عدد/خط‌تیره (مثل example.com یا 1-2-3-4.nip.io)
DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def valid_slug(s: str) -> bool:
    """شناسه از نظر قالب درست است (بدون چک رزرو)."""
    return bool(SLUG_RE.match(s))


def valid_email(s: str) -> bool:
    return bool(EMAIL_RE.match(s))


def valid_domain(s: str) -> bool:
    return bool(DOMAIN_RE.match(s))


# پسوندهای غیرعمومی — Let's Encrypt از ۲۰۲۵ دامنهٔ ایمیل contact را اعتبارسنجی
# می‌کند و دامنهٔ بدون پسوند عمومی را با invalidContact رد می‌کند؛ ردشدنِ ثبت
# حساب ACME یعنی هیچ گواهی‌ای صادر نمی‌شود (بلاکِ خاموش — خطا فقط در لاگ ترافیک).
NONPUBLIC_TLDS = frozenset((
    "local", "localhost", "internal", "invalid", "test", "example",
    "lan", "home", "corp", "priv", "private", "arpa",
))


def acme_email_error(s: str) -> str | None:
    """خطای ایمیل contact گواهی Let's Encrypt — یا None یعنی سالم (v0.3.16).

    فراتر از valid_email: دامنهٔ ایمیل باید پسوند عمومی داشته باشد.
    درس میدانی: «mohsen@parastoo.local» از ویزارد گذشت، apply_domain آن را
    در traefik.yml نوشت و Let's Encrypt ثبت حساب ACME را رد کرد — دامنه
    فعال بود ولی HTTPS تا اصلاح دستی ایمیل بالا نمی‌آمد.
    """
    email = (s or "").strip()
    if not valid_email(email):
        return "email_invalid"
    tld = email.rsplit("@", 1)[1].lower().rstrip(".").rsplit(".", 1)[-1]
    if tld in NONPUBLIC_TLDS:
        return "email_nonpublic_domain"
    return None


def site_slug_error(s: str) -> str | None:
    """خطای شناسهٔ سایت برای انتشار/فعال‌سازی مارکت — یا None یعنی سالم.

    قاعدهٔ تاریخی پرستو (حفظ شده): شناسهٔ رزروشده هم در انتشار سایت همان
    slug_invalid برمی‌گردد، نه slug_reserved (پیام رزرو فقط در ساخت پروژه است).
    """
    if not valid_slug(s) or s in settings.RESERVED_SLUGS:
        return "slug_invalid"
    return None


def project_slug_error(s: str) -> str | None:
    """خطای شناسهٔ پروژه هنگام ساخت — یا None یعنی سالم."""
    if not valid_slug(s):
        return "slug_invalid"
    if s in settings.RESERVED_SLUGS:
        return "slug_reserved"
    return None
