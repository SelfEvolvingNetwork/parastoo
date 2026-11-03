"""پنل ادمین: آمار سیستم، مدیریت کاربران و شارژ کیف پول، مدیریت همه سایت‌ها."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import dockerops, gitea
from ..config import settings
from ..deps import admin_guard, ctx, redirect, render
from ..db import get_db
from ..models import Build, Project, Site, User, WalletTx

router = APIRouter()


@router.get("/admin")
def admin_page(request: Request, user=Depends(admin_guard), db: Session = Depends(get_db)):
    stats = {
        "users": db.query(User).count(),
        "projects": db.query(Project).count(),
        "sites_running": db.query(Site).filter(Site.status == "running").count(),
        "sites_total": db.query(Site).count(),
        "builds_total": db.query(Build).count(),
        "builds_failed": db.query(Build).filter(Build.status == "failed").count(),
    }
    users = db.query(User).order_by(User.id.asc()).all()
    sites = (db.query(Site).order_by(Site.id.desc()).limit(50).all())
    system = {
        "docker": dockerops.available(),
        "gitea": gitea.GiteaClient().healthy(),
        "docker_stats": dockerops.system_stats(),
    }
    return render("admin.html", ctx(request, user, stats=stats, users=users,
                                    sites=sites, system=system))


@router.post("/admin/users/{user_id}/credit")
def admin_credit(request: Request, user_id: int,
                 amount: str = Form(...),
                 note: str = Form(""),
                 user=Depends(admin_guard), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        return redirect("/admin?m=not_found")
    try:
        amt = int(amount.strip())
    except ValueError:
        return redirect("/admin?m=error")
    if amt == 0:
        return redirect("/admin?m=error")
    target.balance += amt
    db.add(WalletTx(user_id=target.id, amount=amt,
                    kind="deposit" if amt > 0 else "admin_credit",
                    note=note.strip() or ("شارژ دستی توسط مدیر" if amt > 0 else "کسر دستی توسط مدیر")))
    db.commit()
    return redirect("/admin?m=credited")


@router.post("/admin/users/{user_id}/toggle")
def admin_toggle_user(request: Request, user_id: int,
                      user=Depends(admin_guard), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        return redirect("/admin?m=not_found")
    if target.id == user.id:
        return redirect("/admin?m=denied")
    target.is_active = not target.is_active
    db.commit()
    return redirect("/admin?m=user_updated")


@router.post("/admin/sites/{site_id}/action")
def admin_site_action(request: Request, site_id: int,
                      action: str = Form(...),
                      user=Depends(admin_guard), db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if site is None:
        return redirect("/admin?m=not_found")
    try:
        if action == "stop":
            dockerops.stop_site(site.slug)
            site.status = "stopped"
        elif action == "start":
            dockerops.start_site(site.slug)
            site.status = "running"
        elif action == "delete":
            dockerops.remove_site(site.slug)
            db.delete(site)
        else:
            return redirect("/admin?m=error")
        db.commit()
    except Exception:
        db.rollback()
        return redirect("/admin?m=publish_failed")
    return redirect("/admin?m=user_updated")
