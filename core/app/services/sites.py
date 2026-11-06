"""چرخهٔ حیات سایت: ساخت+اجرا، روشن/خاموش/حذف، وضعیت زندهٔ کانتینر.

ساخت سایت یک‌جا تعریف شده چون تا v0.2.24 دو مسیر (انتشار از پروژه و
فعال‌سازی مارکت‌پلیس) همین منطق را کپی‌کرده بودند و هر رفع باگ باید دو جا
اعمال می‌شد.
"""
from sqlalchemy.orm import Session

from .. import dockerops, domains, stacks
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
    """وضعیت واقعی کانتینر هر سایت — وقتی داکر در دسترس نیست، خالی."""
    if not sites or not dockerops.available():
        return {}
    return {s.id: dockerops.container_status(s.slug) for s in sites}


# ---------------------------------------------------------------- ساخت و اجرا

def publish_from_project(db: Session, owner: User, project, site_slug_raw: str,
                         tier_key: str, port_str: str = ""):
    """انتشار سایت از پروژهٔ خود کاربر.

    برمی‌گرداند: (site|None, flash_key) — flash_key == "" یعنی موفق.
    """
    if not project.latest_image:
        return None, "image_missing"
    s = slugify(site_slug_raw)
    err = site_slug_error(s)
    if err:
        return None, err
    if db.query(Site).filter(Site.slug == s).first():
        return None, "slug_taken"
    if tier_key not in settings.TIERS:
        tier_key = "nano"
    # صداقت کیف پول (v0.3.27): با موجودیِ کمتر از یک ساعتِ پلن، اصلاً منتشر نکن —
    # قبلاً انتشار سبز می‌داد و ورکر بیلینگ در همان دقیقهٔ اول خاموشش می‌کرد
    if not billing_svc.balance_ok(owner, tier_key):
        return None, "no_balance"

    site, ok = create_and_run(
        db, project=project, owner_id=owner.id, site_slug=s,
        tier_key=tier_key, internal_port=port_for_project(project, port_str),
        image=project.latest_image,
    )
    return site, ("" if ok else "publish_failed")


def create_and_run(db: Session, *, project, owner_id: int, site_slug: str,
                   tier_key: str, internal_port: int, image: str,
                   pull_creds: tuple[str, str] | None = None):
    """ساخت ردیف سایت + اجرای کانتینر.

    خرابی داکر رکورد را نمی‌کشد: سایت با status=error می‌ماند تا کاربر ببیند
    و دوباره تلاش کند. برمی‌گرداند: (site, ok)
    """
    site = Site(
        project_id=project.id,
        owner_id=owner_id,
        slug=site_slug,
        subdomain=f"{site_slug}.{domains.apps()}",
        container_name=f"parastoo-site-{site_slug}",
        internal_port=internal_port,
        tier=tier_key,
        status="running",
        image=image,
    )
    db.add(site)
    db.commit()
    db.refresh(site)

    try:
        dockerops.run_site(
            image=site.image, slug=site.slug, subdomain=site.subdomain,
            port=site.internal_port, tier=site.tier, pull_creds=pull_creds,
        )
        return site, True
    except Exception:
        site.status = "error"
        db.commit()
        return site, False


def port_for_project(project, port_str: str = "") -> int:
    """پورت داخلی: عدد معتبر کاربر، وگرنه پورت استاندارد استک."""
    port = stacks.default_port(project.stack)
    if port_str.strip().isdigit():
        port = max(1, min(65535, int(port_str)))
    return port


# ---------------------------------------------------------------- کنترل

def start(db: Session, site: Site) -> str:
    """روشن کردن سایت — کاربر بدهکار یا بدون بودجهٔ یک ساعت اجازهٔ روشن‌کردن ندارد.
    کلید فلش برمی‌گردد."""
    # قبلاً فقط منفی‌بودن چک می‌شد: با موجودی صفر، سایت روشن و بلافاصله
    # توسط بیلینگ خاموش می‌شد — چرخهٔ بسته‌ای که کاربر علتش را نمی‌دید
    if not billing_svc.balance_ok(site.owner, site.tier):
        return "no_balance"
    try:
        dockerops.start_site(site.slug)
        site.status = "running"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return "site_start_failed"
    return "site_started"


def stop(db: Session, site: Site) -> str:
    try:
        dockerops.stop_site(site.slug)
        site.status = "stopped"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return "site_stop_failed"
    return "site_stopped"


def delete(db: Session, site: Site) -> str:
    """حذف سایت — پاکسازی داکر best-effort است؛ رکورد همیشه پاک می‌شود."""
    try:
        dockerops.remove_site(site.slug)
    except Exception:
        pass
    db.delete(site)
    db.commit()
    return "site_deleted"
