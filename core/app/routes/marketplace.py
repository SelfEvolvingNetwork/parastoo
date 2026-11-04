"""مارکت‌پلیس ایمیج‌ها — لایهٔ نازک HTTP؛ پرداخت/ساخت سایت در services/market.py."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render
from ..models import Project
from ..services import market as market_svc

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

    site, err = market_svc.activate_project(db, user, project, site_slug, tier)
    if not err:
        return redirect("/sites?m=purchased")
    if err == "no_balance":
        return redirect("/wallet?m=no_balance")
    if err == "publish_failed":
        return redirect("/sites?m=publish_failed")
    return redirect(f"/marketplace?m={err}")
