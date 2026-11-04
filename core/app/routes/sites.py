"""سایت‌های منتشرشده کاربر: لیست، روشن/خاموش، حذف."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import dockerops
from ..config import settings
from ..deps import ctx, csrf_guard, redirect, render
from ..db import get_db
from ..models import Site

router = APIRouter()


def _get_site(db: Session, user, site_id: int) -> Site | None:
    site = db.get(Site, site_id)
    if site is None or (site.owner_id != user.id and not user.is_admin):
        return None
    return site


@router.get("/sites")
def sites_list(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    items = (db.query(Site)
             .filter(Site.owner_id == user.id)
             .order_by(Site.id.desc())
             .all())
    live = {}
    if items and dockerops.available():
        for s in items:
            live[s.id] = dockerops.container_status(s.slug)
    monthly = sum(
        settings.TIERS.get(s.tier, settings.TIERS["nano"])["price"] * 24 * 30
        for s in items if s.status == "running"
    )
    return render("sites.html", ctx(request, user, items=items, live=live, monthly=monthly))


@router.post("/sites/{site_id}/start")
def site_start(request: Request, site_id: int,
               user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = _get_site(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    if site.owner.balance < settings.DEBT_LIMIT:
        return redirect("/sites?m=debt")
    try:
        dockerops.start_site(site.slug)
        site.status = "running"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return redirect("/sites?m=publish_failed")
    return redirect("/sites?m=site_started")


@router.post("/sites/{site_id}/stop")
def site_stop(request: Request, site_id: int,
              user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = _get_site(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    try:
        dockerops.stop_site(site.slug)
        site.status = "stopped"
        db.commit()
    except Exception:
        site.status = "error"
        db.commit()
        return redirect("/sites?m=publish_failed")
    return redirect("/sites?m=site_stopped")


@router.post("/sites/{site_id}/delete")
def site_delete(request: Request, site_id: int,
                user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = _get_site(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    try:
        dockerops.remove_site(site.slug)
    except Exception:
        pass
    db.delete(site)
    db.commit()
    return redirect("/sites?m=site_deleted")
