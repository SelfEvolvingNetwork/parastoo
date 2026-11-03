"""مارکت‌پلیس ایمیج‌ها: فهرست عمومی + فعال‌سازی (خرید یا رایگان) برای کاربران دیگر."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import dockerops, domains, stacks
from ..config import settings
from ..deps import ctx, csrf_guard, redirect, render
from ..db import get_db
from ..models import Project, Site, WalletTx
from ..security import slugify

from .projects import SLUG_RE

router = APIRouter()


@router.get("/marketplace")
def marketplace(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    items = (db.query(Project)
             .filter(Project.is_public.is_(True))
             .filter(Project.latest_image.isnot(None))
             .order_by(Project.installs.desc(), Project.id.desc())
             .all())
    return render("marketplace.html", ctx(request, user, items=items))


@router.post("/marketplace/{project_id}/use")
def marketplace_use(
    request: Request, project_id: int,
    site_slug: str = Form(...),
    tier: str = Form("nano"),
    user=Depends(csrf_guard),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or not project.latest_image:
        return redirect("/marketplace?m=not_found")
    if not project.is_public and project.owner_id != user.id:
        return redirect("/marketplace?m=denied")

    s = slugify(site_slug)
    if not SLUG_RE.match(s) or s in settings.RESERVED_SLUGS:
        return redirect(f"/marketplace?m=slug_invalid")
    if db.query(Site).filter(Site.slug == s).first():
        return redirect(f"/marketplace?m=slug_taken")
    if tier not in settings.TIERS:
        tier = "nano"

    seller = project.owner
    price = project.price if project.owner_id != user.id else 0

    if price > user.balance:
        return redirect("/wallet?m=no_balance")

    # پرداخت: خریدار قیمت را می‌دهد، فروشنده بعد از کمیسیون می‌گیرد
    if price > 0:
        commission = price * settings.COMMISSION_PERCENT // 100
        user.balance -= price
        seller.balance += price - commission
        db.add(WalletTx(user_id=user.id, amount=-price, kind="purchase",
                        note=f"خرید ایمیج {project.name} از مارکت‌پلیس"))
        db.add(WalletTx(user_id=seller.id, amount=price - commission, kind="sale",
                        note=f"فروش ایمیج {project.name} (کمیسیون {settings.COMMISSION_PERCENT}٪)"))
        project.installs += 1

    subdomain = f"{s}.{domains.apps()}"
    # پورت داخلی از استک پروژه می‌آید (static=80، node/python=3000)؛
    # اگر فروشنده موقع انتشار پورت سفارشی داده باشد، همان استفاده می‌شود
    src_site = (db.query(Site)
                .filter(Site.project_id == project.id)
                .order_by(Site.id.desc())
                .first())
    internal_port = src_site.internal_port if src_site else stacks.default_port(project.stack)
    site = Site(
        project_id=project.id,
        owner_id=user.id,
        slug=s,
        subdomain=subdomain,
        container_name=f"parastoo-site-{s}",
        internal_port=internal_port,
        tier=tier,
        status="running",
        image=project.latest_image,
    )
    db.add(site)
    db.commit()
    db.refresh(site)

    try:
        # اگر ایمیج خصوصی است، با اعتبار مالکش pull می‌شود (سمت سرور؛ خریدار نیازی به رجیستری ندارد)
        creds = (seller.gitea_username, seller.gitea_token) if seller.gitea_token else None
        dockerops.run_site(
            image=site.image, slug=site.slug, subdomain=site.subdomain,
            port=site.internal_port, tier=site.tier, pull_creds=creds,
        )
    except Exception:
        site.status = "error"
        db.commit()
        return redirect("/sites?m=publish_failed")
    return redirect("/sites?m=purchased")
