"""چرخهٔ حیات سایت‌ها (v0.4.0) — سایت دیگر کانتینر نیست؛ نقطهٔ ورود عمومی است.

سایت = زیردامنه‌ای که به سرویس عمومیِ پروژه روت می‌شود. منابع و اجرا در
سطح سرویس است، پس:
- انتشار سایت          → افزودن ردیف سایت + استقرار مجدد پروژه (لیبل ترافیک تازه)
- روشن/خاموش سایت      → روشن/خاموش کردن کل پروژه (کانتینرها مشترک‌اند)
- حذف سایت             → حذف ردیف؛ اگر آخرین سایت پروژه بود، کانتینرها هم
                         خاموش می‌شوند (هیچ چیزِ بی‌روته هزینه نسازد)
ساخت/روشن‌کردن هر مسیر، گارد کیف پول «یک ساعتِ کل سرویس‌های پروژه» را دارد.
"""
from sqlalchemy.orm import Session

from .. import dockerops, domains
from ..config import settings
from ..models import Site, User
from ..security import slugify
from ..validation import site_slug_error
from . import billing as billing_svc


def site_for(db: Session, user: User, site_id: int) -> Site | None:
    """سایت اگر مالکش فراخواننده باشد (یا ادمین) — وگرنه None (IDOR-safe)."""
    site = db.get(Site, site_id)
    if site is None or (site.owner_id != user.id and not user.is_admin):
        return None
    return site


def live_statuses(sites) -> dict[int, str | None]:
    """وضعیت زندهٔ کانتینرها به دانهٔ پروژه — وقتی داکر در دسترس نیست، خالی."""
    if not sites or not dockerops.available():
        return {}
    out: dict[int, str | None] = {}
    for s in sites:
        st = dockerops.project_container_status(s.project_id)
        # وضعیت نمایشی سایت = وضعیت سرویس عمومیِ پروژه
        pub = next((svc for svc in s.project.services if svc.is_public), None)
        out[s.id] = st.get(pub.name) if pub else None
    return out


def _deploy(db: Session, project, pull_creds=None) -> None:
    """استقرار پروژه + همگام‌سازی وضعیت سرویس‌ها و سایت‌ها."""
    dockerops.deploy_project(project, project.services, project.sites,
                             pull_creds=pull_creds)
    for svc in project.services:
        if svc.image:
            svc.status = "running"
    for site in project.sites:
        site.status = "running"


def publish_from_project(db: Session, owner: User, project, site_slug_raw: str,
                         tier_key: str = "nano"):
    """انتشار سایت از پروژهٔ خود کاربر.

    tier برای پروژه‌های تک-سرویسیِ بدون مانیفست به همان سرویس اعمال می‌شود؛
    پروژه‌های مانیفست‌دار tier سرویس‌هایشان را از مانیفست دارند.
    برمی‌گرداند: (site|None, flash_key) — flash_key == "" یعنی موفق.
    """
    services = [s for s in project.services if s.image]
    if not services:
        return None, "image_missing"
    s = slugify(site_slug_raw)
    err = site_slug_error(s)
    if err:
        return None, err
    if db.query(Site).filter(Site.slug == s).first():
        return None, "slug_taken"
    # صداقت کیف پول: با موجودیِ کمتر از یک ساعتِ «کل» سرویس‌ها اصلاً منتشر نکن
    if not billing_svc.balance_ok_total(owner, services):
        return None, "no_balance"
    if not project.manifest_json and len(services) == 1 \
            and tier_key in settings.TIERS:
        services[0].tier = tier_key

    site = Site(project_id=project.id, owner_id=owner.id, slug=s,
                subdomain=f"{s}.{domains.apps()}", status="running")
    db.add(site)
    db.commit()
    db.refresh(site)
    try:
        _deploy(db, project)
        # درس میدانی v0.4.5 (سفر کامل پل): get_db در پایان فقط close می‌کند؛
        # بدون کامیت صریح، running شدن سرویس‌ها در مسیر موفق گم می‌شد و
        # pst status پس از publish «stopped» نشان می‌داد با کانتینرِ زنده.
        db.commit()
        return site, ""
    except Exception:
        site.status = "error"
        for svc in project.services:
            if svc.image:
                svc.status = "error"
        db.commit()
        return site, "publish_failed"


# ---------------------------------------------------------------- کنترل

def start(db: Session, site: Site) -> str:
    """روشن کردن سایت = روشن کردن کل پروژه (سرویس‌هایش مشترک‌اند)."""
    services = [s for s in site.project.services if s.image]
    if not billing_svc.balance_ok_total(site.owner, services):
        return "no_balance"
    try:
        dockerops.start_project(site.project_id)
        for svc in services:
            svc.status = "running"
        site.status = "running"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return "site_start_failed"
    return "site_started"


def stop(db: Session, site: Site) -> str:
    try:
        dockerops.stop_project(site.project_id)
        for svc in site.project.services:
            if svc.image:
                svc.status = "stopped"
        for other in site.project.sites:
            other.status = "stopped"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return "site_stop_failed"
    return "site_stopped"


def delete(db: Session, site: Site) -> str:
    """حذف سایت — روتینگ می‌رود؛ اگر آخرین سایت پروژه بود کانتینرها هم خاموش."""
    project = site.project
    db.delete(site)
    db.commit()
    remaining = [s for s in project.sites]
    if not remaining:
        try:
            dockerops.stop_project(project.id)
            for svc in project.services:
                if svc.image:
                    svc.status = "stopped"
            db.commit()
        except Exception:
            db.rollback()
    else:
        # روتر این سایت از لیبل‌های سرویس عمومی باید برود — استقرار مجدد
        try:
            _deploy(db, project)
        except Exception:
            pass  # رکورد حذف شده؛ استقرار مجدد در بیلد بعدی هم درست می‌شود
    return "site_deleted"
