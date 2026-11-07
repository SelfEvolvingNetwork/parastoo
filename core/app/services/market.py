"""فعال‌سازی اپ مارکت‌پلیس: پرداخت از کیف پول + نمونه‌سازی سرویس‌های خریدار (v0.4.0).

قواعد پول (تک‌منبع از v0.3.0):
- پروژهٔ خودِ کاربر همیشه رایگان است (price=0) — تست‌کردن اپ خودش نباید هزینه داشته باشد.
- خریدار قیمت کامل را می‌دهد؛ فروشنده پس از کمیسیون پرستو مبلغ را می‌گیرد.
- هر دو طرف یک WalletTx می‌گیرند (دفتر کل)؛ نصب‌ها شمارش می‌شوند.

معماری سرویس‌محور: اپِ چند-کانتینری هم به همین سادگی نصب می‌شود — نصب = ساخت
پروژهٔ مالکِ خریدار (بدون مخزن؛ source_project_id به فروشنده اشاره می‌کند) +
کپی ردیف‌های سرویس فروشنده با ایمیج‌های همان تگ + استقرار روی شبکهٔ خصوصیِ
خریدار + سایتِ زیردامنهٔ خریدار. بین خریداران و فروشنده هیچ runtime مشترکی
نیست؛ به‌روزرسانی اپ فروشنده در بیلد بعدی به همهٔ نصب‌ها منتشر می‌شود.
"""
from sqlalchemy.orm import Session

from .. import domains
from ..config import settings
from ..models import Project, Service, Site, User, WalletTx
from ..security import gen_secret, slugify
from ..validation import site_slug_error
from . import billing as billing_svc
from . import sites as sites_svc


def activate_project(db: Session, buyer: User, project: Project,
                     site_slug_raw: str):
    """خرید/فعال‌سازی اپ مارکت برای خریدار + نمونه‌سازی کامل سرویس‌هایش.

    برمی‌گرداند: (site|None, flash_key) — flash_key == "" یعنی موفق.
    """
    if not project.is_public and project.owner_id != buyer.id:
        return None, "denied"
    services_src = [s for s in project.services if s.image]
    if not services_src:
        return None, "image_missing"

    s = slugify(site_slug_raw)
    err = site_slug_error(s)
    if err:
        return None, err
    if db.query(Site).filter(Site.slug == s).first() or \
            db.query(Project).filter(Project.slug == s).first():
        return None, "slug_taken"

    seller = project.owner
    price = project.price if project.owner_id != buyer.id else 0
    if price > buyer.balance:
        return None, "no_balance"
    # v0.3.27/0.4.0: خرید کافی نیست — بعد از خرید کسر ساعتیِ «کل سرویس‌ها» شروع می‌شود
    if not billing_svc.balance_ok_total(buyer, services_src):
        return None, "no_balance"

    if price > 0:
        commission = price * settings.COMMISSION_PERCENT // 100
        buyer.balance -= price
        seller.balance += price - commission
        db.add(WalletTx(user_id=buyer.id, amount=-price, kind="purchase",
                        note=f"خرید اپ {project.name} از مارکت‌پلیس"))
        db.add(WalletTx(user_id=seller.id, amount=price - commission, kind="sale",
                        note=f"فروش اپ {project.name} (کمیسیون {settings.COMMISSION_PERCENT}٪)"))
        project.installs += 1

    # پروژهٔ نصبِ خریدار: بدون مخزن، سرویس‌ها کپی فروشنده با همان تگ ایمیج
    install = Project(
        owner_id=buyer.id,
        name=f"{project.name} — نصب {s}",
        slug=s,
        stack="",
        description=f"نصب اپ مارکت‌پلیس «{project.name}»",
        source_project_id=project.id,
        webhook_secret=gen_secret(16),
        repo_status="ready",  # مخزن ندارد — bootstrap سراغش نمی‌رود
    )
    db.add(install)
    db.flush()
    for src in services_src:
        db.add(Service(project_id=install.id, name=src.name, image=src.image,
                       port=src.port, command=src.command, env_json=src.env_json,
                       volumes_json=src.volumes_json, is_public=src.is_public,
                       tier=src.tier, status="stopped",
                       container_name=""))
    site = Site(project_id=install.id, owner_id=buyer.id, slug=s,
                subdomain=f"{s}.{domains.apps()}", status="running")
    db.add(site)
    db.commit()
    db.refresh(install)
    db.refresh(site)

    try:
        # ایمیج‌های خصوصی فروشنده با اعتبار مالکش pull می‌شود (سمت سرور؛
        # خریدار به رجیستری دسترسی ندارد) — روی دیمون محلی همگام می‌ماند
        sites_svc._deploy(db, install,
                          pull_creds=(seller.gitea_username, seller.gitea_token)
                          if seller.gitea_token else None)
        return site, ""
    except Exception:
        site.status = "error"
        for svc in install.services:
            if svc.image:
                svc.status = "error"
        db.commit()
        return site, "publish_failed"
