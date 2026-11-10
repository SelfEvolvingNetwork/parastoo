"""کاتالوگ خدمات فروش‌پذیر، پارامترهای کسب‌وکار و تپش‌نگار پایداری (v0.5.19).

سه قرارداد این ماژول (مشورت ۲ مشاور، 2026-11-10):
۱) قیمت «بازارمحور» است نه هزینه‌محور — اعداد پیش‌فرض از قیمت‌های رایج
   بازار ایران ۱۴۰۵ آمده (منابع: docs/SUSTAINABILITY.fa.md §۳) و ادمین از
   /admin/business بازنویسی می‌کند. کف هزینه‌محور فقط در سند پایداری می‌ماند.
۲) دادهٔ کسب‌وکار (هزینهٔ سرور، سررسیدها، حقوق) فقط از فرم ادمین وارد
   می‌شود — نه چت؛ مصرف‌کننده‌های اجرایی (پایش، یادآور سررسید، سربه‌سر)
   از همین‌جا می‌خوانند.
۳) تپش‌نگار هر ۱۰ دقیقه یک ردیف صادقانه می‌نویسد؛ /status فقط همین
   شواهد را نمایش می‌دهد — قولِ پایداری با سابقهٔ ثبت‌شده، نه با ادعا
   (وصیت ۱۰ میثاق: اعلام پایداری فقط با سابقهٔ پایدار).
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import dockerops
from ..db import SessionLocal, db_now
from ..models import Heartbeat, Setting

# --- کاتالوگ خدمات (تک‌منبع؛ قیمت‌ها تومان/ماهانه، بازارمحور ۱۴۰۵) ---
SERVICES = (
    {"key": "site-basic", "code": "S1", "title": "میزبانی سایت آماده",
     "desc": "سایت شما (فروشگاه، شرکت یا کلینیک) روی زیردامنهٔ اختصاصی، با پنل مدیریت و پشتیبانی فارسی.",
     "features": ("زیردامنهٔ اختصاصی با HTTPS خودکار", "پنل مدیریت سایت", "بیلد و انتشار از پنل"),
     "unit": "ماهانه", "default_price": 990_000, "status": "live"},
    {"key": "site-domain", "code": "S1+S2", "title": "میزبانی + دامنهٔ اختصاصی",
     "desc": "همهٔ امکان پلن پایه + اتصال دامنهٔ اختصاصی شما، DNS و مدیریت گواهی.",
     "features": ("همهٔ امکانات پلن پایه", "دامنهٔ اختصاصی + DNS", "گواهی TLS خودکار"),
     "unit": "ماهانه", "default_price": 1_490_000, "status": "live"},
    {"key": "dev-git", "code": "S3", "title": "بیلد و انتشار از گیت",
     "desc": "برای توسعه‌دهنده‌ها: push بزنید، بیلد داکر و انتشار خودکار انجام می‌شود.",
     "features": ("مخزن گیت اختصاصی", "بیلد کل-یا-هیچ با بازگشت امن", "رجیستری داخلی و سریع"),
     "unit": "ماهانه", "default_price": 1_990_000, "status": "live"},
    {"key": "dev-agent", "code": "S4", "title": "اشتراک عامل توسعه‌دهنده",
     "desc": "توسعه و نگهداری سایت شما توسط عامل هوشمند از مسیر تیکت — تمایز اصلی پرستو.",
     "features": ("تیکت‌محور؛ بدون نیاز به دانش فنی", "به‌روزرسانی و رفع اشکال توسط عامل", "گزارش شفاف هر تغییر"),
     "unit": "ماهانه", "default_price": 3_490_000, "status": "live"},
    {"key": "managed-sla", "code": "S5", "title": "نگهداری ویژه + SLA",
     "desc": "پشتیبان‌گیری منظم و زمان پاسخ تضمینی — پس از راستی‌آزمایی بکاپ پایگاه‌داده فعال می‌شود.",
     "features": ("پشتیبان‌گیری منظم", "زمان پاسخ تضمینی", "گزارش ماهانهٔ پایداری"),
     "unit": "ماهانه", "default_price": 1_490_000, "status": "soon"},
)

PRICE_KEY_PREFIX = "price_"

LEAD_STATUSES = ("new", "contacted", "won", "lost")

# --- پارامترهای کسب‌وکار (فقط از فرم ادمین /admin/business) ---
BUSINESS_FIELDS = (
    {"key": "server_cost_monthly", "label": "هزینهٔ ماهانهٔ سرور (تومان)", "kind": "int"},
    {"key": "server_due_date", "label": "تاریخ سررسید تمدید سرور (YYYY-MM-DD)", "kind": "date"},
    {"key": "domain_due_date", "label": "تاریخ سررسید تمدید دامنه (YYYY-MM-DD)", "kind": "date"},
    {"key": "advisor_salary_monthly", "label": "حقوق ماهانهٔ مشاور (تومان)", "kind": "int"},
)


def get_param(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else default


def set_param(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def price_of(db: Session, svc: dict) -> int:
    row = db.get(Setting, PRICE_KEY_PREFIX + svc["key"])
    if row is not None and row.value.isdigit():
        return int(row.value)
    return svc["default_price"]


def set_price(db: Session, key: str, raw: str) -> None:
    """عدد = بازنویسی؛ خالی = بازگشت به پیش‌فرض بازار (حذف بازنویسی)."""
    row = db.get(Setting, PRICE_KEY_PREFIX + key)
    raw = (raw or "").strip()
    if raw.isdigit():
        set_param(db, PRICE_KEY_PREFIX + key, raw)
    elif row is not None:
        db.delete(row)


def catalog(db: Session) -> list[dict]:
    """کاتالوگ با قیمت نهایی (بازنویسی ادمین یا پیش‌فرض بازار)."""
    return [{**svc, "price": price_of(db, svc)} for svc in SERVICES]


def breakeven(db: Session, arpu: int) -> int | None:
    """تعداد مشتری لازم برای پوشش هزینه‌های ماهانهٔ ثبت‌شده در فرم ادمین."""
    try:
        fixed = int(get_param(db, "server_cost_monthly") or 0) \
            + int(get_param(db, "advisor_salary_monthly") or 0)
    except ValueError:
        return None
    if fixed <= 0 or arpu <= 0:
        return None
    return -(-fixed // arpu)  # ceil


# --- تپش‌نگار پایداری ---
HEARTBEAT_INTERVAL = 600  # ثانیه (۱۰ دقیقه)
HEARTBEAT_KEEP = 4_464    # ~۳۱ روز نمونه با فاصلهٔ ۱۰ دقیقه


def heartbeat_once() -> None:
    """یک ردیف صادقانه: دیتابیس و داکر سالم‌اند؟ — هر استثنا یعنی ok=False."""
    ok, parts = True, []
    db = SessionLocal()
    try:
        try:
            db.query(Setting).limit(1).all()
            parts.append("db=ok")
        except Exception as e:
            ok = False
            parts.append(f"db=fail({e.__class__.__name__})")
        try:
            parts.append("docker=ok" if dockerops.available() else "docker=off")
        except Exception:
            ok = False
            parts.append("docker=fail")
        db.add(Heartbeat(ok=ok, detail=" ".join(parts)))
        db.commit()
        cutoff = db_now() - timedelta(seconds=HEARTBEAT_KEEP * HEARTBEAT_INTERVAL)
        db.query(Heartbeat).filter(Heartbeat.created_at < cutoff).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def status_summary(db: Session) -> dict:
    """جمع‌بندی شواهد برای /status — فقط اعداد ثبت‌شده، بدون ادعا."""
    last = (db.query(Heartbeat)
            .order_by(Heartbeat.id.desc()).first())
    first = (db.query(Heartbeat)
             .order_by(Heartbeat.id.asc()).first())
    last_red = (db.query(Heartbeat)
                .filter(Heartbeat.ok.is_(False))
                .order_by(Heartbeat.id.desc()).first())
    week_ago = db_now() - timedelta(days=7)
    rows_week = (db.query(Heartbeat)
                 .filter(Heartbeat.created_at >= week_ago).all())
    total, green = len(rows_week), sum(1 for r in rows_week if r.ok)
    streak_h = None
    if last is not None and last.ok:
        anchors = [r.created_at for r in (last_red, first) if r is not None]
        if anchors:
            streak_h = round((db_now() - max(anchors)).total_seconds() / 3600, 1)
    rows = (db.query(Heartbeat)
            .order_by(Heartbeat.id.desc()).limit(20).all())
    return {"last": last, "total": total, "green": green,
            "pct": round(100 * green / total, 1) if total else None,
            "streak_h": streak_h, "rows": rows}
