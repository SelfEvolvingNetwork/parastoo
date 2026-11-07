"""مارکت‌پلیس اپ‌ها — لایهٔ نازک HTTP؛ پرداخت/نمونه‌سازی در services/market.py.

از v0.4.0 اپ‌های چند-کانتینری هم در مارکت فروخته می‌شوند: هر کارت تعداد
کانتینرها و جمع هزینهٔ ساعتی سرویس‌هایش را نشان می‌دهد.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render
from ..models import Project
from ..services import billing as billing_svc
from ..services import market as market_svc

router = APIRouter()


@router.get("/marketplace")
def marketplace(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    items = (db.query(Project)
             .filter(Project.is_public.is_(True))
             .filter(Project.source_project_id.is_(None))
             .order_by(Project.installs.desc(), Project.id.desc())
             .all())
    # فقط اپ‌هایی که حداقل یک سرویسِ بیلدشده دارند + هزینهٔ ساعتی هر کارت
    items = [p for p in items if any(s.image for s in p.services)]
    costs = {p.id: billing_svc.hourly_cost([s for s in p.services if s.image])
             for p in items}
    counts = {p.id: len([s for s in p.services if s.image]) for p in items}
    return render("marketplace.html", ctx(request, user, items=items,
                                          costs=costs, counts=counts))


@router.post("/marketplace/{project_id}/use")
def marketplace_use(
    request: Request, project_id: int,
    site_slug: str = Form(...),
    user=Depends(csrf_guard),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None or not any(s.image for s in project.services):
        return redirect("/marketplace?m=not_found")

    site, err = market_svc.activate_project(db, user, project, site_slug)
    if not err:
        return redirect("/sites?m=purchased")
    if err == "no_balance":
        return redirect("/wallet?m=no_balance")
    if err == "publish_failed":
        # نصب ثبت شده ولی کانتینرها روشن نشده — پیام صادقانهٔ جدا از «انتشار ناموفق»
        return redirect("/sites?m=purchase_needs_start")
    return redirect(f"/marketplace?m={err}")
