"""صورتحساب — قیمت‌گذاری پلن‌ها، برآورد هزینه، کسر ساعتی منابع (سرویس‌محور v0.4.0).

واحد مصرف‌کنندهٔ منابع «سرویس» است (نه سایت و نه پروژه): جمع سرویس‌های
running یک پروژه = هزینهٔ آن پروژه. تک‌منبع فرمول قیمت از v0.3.0 حفظ شده.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_now
from ..models import Service, User, WalletTx


def tier(key: str) -> dict:
    """پلن منابع با فالبک امن به نانو (تییر ناشناخته دیگر ۵۰۰ نمی‌دهد)."""
    return settings.TIERS.get(key, settings.TIERS["nano"])


def price_hourly(tier_key: str) -> int:
    """قیمت ساعتی یک پلن (تومان)."""
    return tier(tier_key)["price"]


def hourly_cost(services) -> int:
    """جمع هزینهٔ ساعتی سرویس‌های داده‌شده (فراخواننده status را فیلتر می‌کند)."""
    return sum(price_hourly(s.tier) for s in services)


def monthly_cost(services) -> int:
    """برآورد ماهانه (ساعتی × ۲۴ × ۳۰) برای نمایش در پنل."""
    return hourly_cost(services) * 24 * 30


def balance_ok(user: User, tier_key: str) -> bool:
    """آیا موجودی برای «یک ساعت نخست» این پلن کافی است؟

    کسر نخست بلافاصله بعد از روشن‌شدن انجام می‌شود، پس روشن‌کردنِ با موجودیِ
    کمتر از قیمت یک ساعت + سقف بدهی، یعنی خاموشی در همان دقیقهٔ اول —
    حادثهٔ میدان 2026-11-06 (انتشار با کیف صفر). از v0.3.27 هر سه مسیر
    روشن‌کردن (انتشار، فعال‌سازی مارکت‌پلیس، start) همین گارد را صدا می‌زنند؛
    در v0.4.0 فراخوانی برای «جمع» پلن‌های سرویس‌های پروژه هم همین تابع است
    (hourly_cost = یک ساعتِ کل).
    """
    return user.balance >= price_hourly(tier_key) + settings.DEBT_LIMIT


def balance_ok_total(user: User, services) -> bool:
    """گارد کیف پول برای مجموع سرویس‌های یک پروژه (یک ساعتِ کل)."""
    return user.balance >= hourly_cost(services) + settings.DEBT_LIMIT


# ---------------------------------------------------------------- کسر ساعتی

def charge_running_services(db: Session, now=None) -> None:
    """یک دور صورتحساب: هر سرویس روشن با گذشت ≥۱ ساعت از آخرین کسر، هزینهٔ
    پلنش را از کیف پول مالکِ پروژه کسر می‌کند؛ رسیدن موجودی زیر DEBT_LIMIT →
    توقف کل پروژه (همهٔ سرویس‌هایش با هم — دانهٔ حیات، پروژه است).

    ورکر (workers.billing_loop) هر ۶۰ ثانیه این را با نشست تازه صدا می‌زند.
    """
    now = now or db_now()
    services = db.query(Service).filter(Service.status == "running").all()
    for svc in services:
        t = tier(svc.tier)
        if svc.last_charged_at:
            elapsed = (now - svc.last_charged_at).total_seconds()
            if elapsed < 3600:
                continue
        user: User = svc.project.owner
        amount = t["price"]
        user.balance -= amount
        db.add(WalletTx(user_id=user.id, amount=-amount, kind="charge",
                        note=f"هزینه منابع سرویس {svc.name} پروژه {svc.project.slug} ({t['label']})"))
        svc.last_charged_at = now
        if user.balance < settings.DEBT_LIMIT:
            _pause_project(db, svc.project, user)
    db.commit()


def _pause_project(db: Session, project, user: User) -> None:
    """توقف کل پروژهٔ بدحساب — خرابی داکر وضعیت را error می‌کند، نه crash ورکر."""
    from .. import dockerops
    try:
        dockerops.stop_project(project.id)
        for s in project.services:
            s.status = "paused"
        for site in project.sites:
            site.status = "paused"
        print(f"[billing] project {project.slug} paused (balance {user.balance})")
    except Exception as e:
        print(f"[billing] stop {project.slug} failed: {e}")
        for s in project.services:
            s.status = "error"
