"""سایت‌های منتشرشده کاربر — لایهٔ نازک HTTP روی services/sites.py."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render
from ..models import Site
from ..services import billing as billing_svc
from ..services import sites as sites_svc

router = APIRouter()


@router.get("/sites")
def sites_list(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    items = (db.query(Site)
             .filter(Site.owner_id == user.id)
             .order_by(Site.id.desc())
             .all())
    live = sites_svc.live_statuses(items)
    running = [s for s in items if s.status == "running"]
    monthly = billing_svc.monthly_cost(running)
    return render("sites.html", ctx(request, user, items=items, live=live, monthly=monthly))


@router.post("/sites/{site_id}/start")
def site_start(request: Request, site_id: int,
               user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = sites_svc.site_for(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    return redirect(f"/sites?m={sites_svc.start(db, site)}")


@router.post("/sites/{site_id}/stop")
def site_stop(request: Request, site_id: int,
              user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = sites_svc.site_for(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    return redirect(f"/sites?m={sites_svc.stop(db, site)}")


@router.post("/sites/{site_id}/delete")
def site_delete(request: Request, site_id: int,
                user=Depends(csrf_guard), db: Session = Depends(get_db)):
    site = sites_svc.site_for(db, user, site_id)
    if site is None:
        return redirect("/sites?m=not_found")
    return redirect(f"/sites?m={sites_svc.delete(db, site)}")
