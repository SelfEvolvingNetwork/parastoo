"""صورتحساب — قیمت‌گذاری پلن‌ها، برآورد هزینه، کسر ساعتی منابع.

تا v0.2.24 فرمول «قیمت پلن سایت» در سه جا تکرار شده بود
(routes/sites.py ماهانه، routes/wallet.py ساعتی، workers.py کسر واقعی)؛
هر تغییر قیمت‌گذاری باید سه جا اعمال می‌شد. حالا تک‌منبع است.
"""
from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_now
from ..models import Site, User, WalletTx


def tier(key: str) -> dict:
    """پلن منابع با فالبک امن به نانو (تییر ناشناخته دیگر ۵۰۰ نمی‌دهد)."""
    return settings.TIERS.get(key, settings.TIERS["nano"])


def price_hourly(tier_key: str) -> int:
    """قیمت ساعتی یک پلن (تومان)."""
    return tier(tier_key)["price"]


def hourly_cost(sites) -> int:
    """جمع هزینهٔ ساعتی سایت‌های داده‌شده (فراخواننده status را فیلتر می‌کند)."""
    return sum(price_hourly(s.tier) for s in sites)


def monthly_cost(sites) -> int:
    """برآورد ماهانه (ساعتی × ۲۴ × ۳۰) برای نمایش در صفحهٔ سایت‌ها."""
    return hourly_cost(sites) * 24 * 30


# ---------------------------------------------------------------- کسر ساعتی

def charge_running_sites(db: Session, now=None) -> None:
    """یک دور صورتحساب: هر سایت روشن با گذشت ≥۱ ساعت از آخرین کسر، هزینه پلن
    را از کیف پول مالک کسر می‌کند؛ رسیدن موجودی زیر DEBT_LIMIT → توقف سایت.

    ورکر (workers.billing_loop) هر ۶۰ ثانیه این را با نشست تازه صدا می‌زند.
    """
    now = now or db_now()
    sites = db.query(Site).filter(Site.status == "running").all()
    for site in sites:
        t = tier(site.tier)
        if site.last_charged_at:
            elapsed = (now - site.last_charged_at).total_seconds()
            if elapsed < 3600:
                continue
        user: User = site.owner
        amount = t["price"]
        user.balance -= amount
        db.add(WalletTx(user_id=user.id, amount=-amount, kind="charge",
                        note=f"هزینه منابع سایت {site.slug} ({t['label']})"))
        site.last_charged_at = now
        if user.balance < settings.DEBT_LIMIT:
            _pause_site(db, site, user)
    db.commit()


def _pause_site(db: Session, site: Site, user: User) -> None:
    """توقف سایت بدحساب — خرابی داکر وضعیت را error می‌کند، نه crash ورکر."""
    from .. import dockerops
    try:
        dockerops.stop_site(site.slug)
        site.status = "paused"
        print(f"[billing] site {site.slug} paused (balance {user.balance})")
    except Exception as e:
        print(f"[billing] stop {site.slug} failed: {e}")
        site.status = "error"
