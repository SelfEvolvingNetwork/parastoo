"""فعال‌سازی ایمیج مارکت‌پلیس: پرداخت از کیف پول + ساخت سایت خریدار.

قواعد پول (تک‌منبع از v0.3.0):
- پروژهٔ خودِ کاربر همیشه رایگان است (price=0) — تست‌کردن ایمیج خودش نباید هزینه داشته باشد.
- خریدار قیمت کامل را می‌دهد؛ فروشنده پس از کمیسیون پرستو مبلغ را می‌گیرد.
- هر دو طرف یک WalletTx می‌گیرند (دفتر کل)؛ نصب‌ها شمارش می‌شوند.
"""
from sqlalchemy.orm import Session

from .. import stacks
from ..config import settings
from ..models import Project, Site, User, WalletTx
from ..security import slugify
from ..validation import site_slug_error
from . import sites as sites_svc


def activate_project(db: Session, buyer: User, project: Project,
                     site_slug_raw: str, tier_key: str):
    """خرید/فعال‌سازی پروژهٔ مارکت برای خریدار + ساخت سایتش.

    فرض: وجود project و latest_image را مسیر قبل از این چک کرده است.
    برمی‌گرداند: (site|None, flash_key) — flash_key == "" یعنی موفق.
    """
    if not project.is_public and project.owner_id != buyer.id:
        return None, "denied"

    s = slugify(site_slug_raw)
    err = site_slug_error(s)
    if err:
        return None, err
    if db.query(Site).filter(Site.slug == s).first():
        return None, "slug_taken"
    if tier_key not in settings.TIERS:
        tier_key = "nano"

    seller = project.owner
    price = project.price if project.owner_id != buyer.id else 0
    if price > buyer.balance:
        return None, "no_balance"

    if price > 0:
        commission = price * settings.COMMISSION_PERCENT // 100
        buyer.balance -= price
        seller.balance += price - commission
        db.add(WalletTx(user_id=buyer.id, amount=-price, kind="purchase",
                        note=f"خرید ایمیج {project.name} از مارکت‌پلیس"))
        db.add(WalletTx(user_id=seller.id, amount=price - commission, kind="sale",
                        note=f"فروش ایمیج {project.name} (کمیسیون {settings.COMMISSION_PERCENT}٪)"))
        project.installs += 1

    # پورت داخلی از استک پروژه می‌آید (static=80، node/python=3000)؛
    # اگر فروشنده موقع انتشار پورت سفارشی داده باشد، همان استفاده می‌شود
    src_site = (db.query(Site)
                .filter(Site.project_id == project.id)
                .order_by(Site.id.desc())
                .first())
    internal_port = src_site.internal_port if src_site else stacks.default_port(project.stack)

    site, ok = sites_svc.create_and_run(
        db, project=project, owner_id=buyer.id, site_slug=s,
        tier_key=tier_key, internal_port=internal_port,
        image=project.latest_image,
        # ایمیج خصوصی با اعتبار مالکش pull می‌شود (سمت سرور؛ خریدار به رجیستری دسترسی ندارد)
        pull_creds=(seller.gitea_username, seller.gitea_token) if seller.gitea_token else None,
    )
    return site, ("" if ok else "publish_failed")
