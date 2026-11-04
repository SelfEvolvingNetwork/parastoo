"""Jinja2 templates setup + helperهای مشترک برای رندر صفحات."""
from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from . import domains
from .config import settings

TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

FLASHES = {
    "welcome": "خوش آمدید! حساب شما ساخته شد.",
    "setup_done": "حساب مدیر ساخته شد. حالا وارد شوید.",
    "reg_closed": "ثبت‌نام بسته است. با مدیر تماس بگیرید.",
    "bad_login": "ایمیل یا رمز عبور اشتباه است.",
    "logged_out": "خارج شدید.",
    "project_created": "پروژه ساخته شد.",
    "slug_invalid": "شناسه فقط باید حروف لاتین کوچک، عدد و خط تیره باشد (۳ تا ۴۰ کاراکتر).",
    "slug_taken": "این شناسه قبلاً استفاده شده است.",
    "slug_reserved": "این شناسه رزرو شده است.",
    "build_queued": "بیلد در صف قرار گرفت. نتیجه را در همین صفحه ببینید.",
    "build_failed_queue": "قرار دادن در صف بیلد ناموفق بود.",
    "published": "سایت منتشر شد!",
    "publish_failed": "انتشار ناموفق بود.",
    "site_started": "سایت روشن شد.",
    "site_stopped": "سایت متوقف شد.",
    "site_deleted": "سایت حذف شد.",
    "project_deleted": "پروژه و همهٔ وابسته‌هایش حذف شد.",
    "project_in_use": "حذف ممکن نیست: سایت‌هایی از این پروژه متعلق به کاربران دیگر فعال است؛ اول باید آن سایت‌ها حذف شوند.",
    "confirm_mismatch": "تأیید حذف درست نیست؛ شناسهٔ پروژه را دقیقاً تایپ کنید.",
    "password_changed": "رمز عبور با موفقیت تغییر کرد.",
    "bad_current": "رمز فعلی درست نیست.",
    "pwd_short": "رمز جدید باید حداقل ۶ کاراکتر باشد.",
    "pwd_mismatch": "تکرار رمز جدید با خودش یکسان نیست.",
    "mp_updated": "تنظیمات مارکت‌پلیس ذخیره شد.",
    "purchased": "ایمیج فعال شد و سایت شما ساخته شد.",
    "no_balance": "موجودی کیف پول کافی نیست. ابتدا کیف پول را شارژ کنید.",
    "debt": "موجودی شما منفی است؛ ابتدا باید کیف پول را شارژ کنید.",
    "credited": "کیف پول شارژ شد.",
    "user_updated": "کاربر به‌روزرسانی شد.",
    "not_found": "موردی پیدا نشد.",
    "denied": "اجازه انجام این کار را ندارید.",
    "csrf": "نشست شما منقضی شده است. صفحه را تازه‌سازی کنید.",
    "error": "خطایی رخ داد. دوباره تلاش کنید.",
    "image_missing": "هنوز ایمیجی برای این پروژه ساخته نشده است؛ ابتدا کد را push کنید.",
    "git_pending": "مخزن گیت در حال ساخت خودکار است؛ بعد از آماده شدن آدرس اینجا نمایش داده می‌شود.",
    "reg_ok": "ثبت‌نام انجام شد. حساب گیت شما به‌صورت خودکار ساخته می‌شود.",
    "bad_token": "توکن نصب درست نیست. توکن فقط در خروجی دستور نصب (ترمینال سرور) چاپ شده است.",
    "domain_applied": "دامنه با موفقیت اعمال شد.",
    "domain_invalid": "قالب دامنه درست نیست. مثال: example.com",
    "dns_pending": "رکوردهای DNS هنوز به این سرور اشاره نمی‌کنند؛ بعد از تنظیم DNS دوباره تلاش کنید (وضعیت را با دکمه بررسی ببینید).",
    "apply_failed": "اعمال دامنه ناموفق بود — پوشه پیکربندی ترافیک در دسترس نیست.",
}


def money(n: int | None) -> str:
    v = int(n or 0)
    return f"{v:,}"


def dt(d: datetime | None) -> str:
    if not d:
        return "—"
    return d.strftime("%Y-%m-%d %H:%M")


def tier_label(key: str) -> str:
    t = settings.TIERS.get(key)
    return f"{t['label']} ({t['cpus']} CPU / {t['mem']}MB)" if t else key


def stack_label(key: str) -> str:
    s = settings.STACKS.get(key)
    return s["label"] if s else key


templates.env.globals.update(
    settings=settings,
    domains=domains,
    FLASHES=FLASHES,
    money=money,
    dt=dt,
    tier_label=tier_label,
    stack_label=stack_label,
)
