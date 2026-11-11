"""پنل ادمین: آمار سیستم، مدیریت کاربران و شارژ کیف پول، مدیریت همه سایت‌ها."""
import traceback

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import dockerops, gitea
from ..deps import admin_guard, ctx, redirect, render
from ..db import db_now, get_db
from ..models import Build, Project, Site, User, WalletTx
from ..services import billing as billing_svc
from ..services import sites as sites_svc

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
    # هزینهٔ ساعتی هر سایت = سرویس‌های running پروژه‌اش (سایت دیگر tier ندارد — v0.4.0)
    hourly_by_site = {
        s.id: billing_svc.hourly_cost(
            [x for x in s.project.services if x.status == "running"])
        for s in sites
    }
    system = {
        "docker": dockerops.available(),
        "gitea": gitea.GiteaClient().healthy(),
        "docker_stats": dockerops.system_stats(),
    }
    return render("admin.html", ctx(request, user, stats=stats, users=users,
                                    sites=sites, hourly_by_site=hourly_by_site,
                                    system=system))


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
    """روشن/خاموش/حذف سایت — همان منطق کاربر (sites_svc) با گارد مالک/ادمین.

    درس میدانی v0.5.1: نسخهٔ پیشین توابع سایتِ حذف‌شده از dockerops را
    می‌خواند (رگرسیون v0.4.0) و AttributeError بی‌صدا به «publish_failed»
    تبدیل می‌شد — دکمه‌های ادمین مرده بودند. از این پس همهٔ مسیرها از
    سرویسِ سایت می‌گذرد و خطای پیش‌بینی‌نشده در لاگ هم می‌آید.
    """
    site = sites_svc.site_for(db, user, site_id)
    if site is None:
        return redirect("/admin?m=not_found")
    if action not in ("stop", "start", "delete"):
        return redirect("/admin?m=error")
    try:
        flash = {"stop": sites_svc.stop, "start": sites_svc.start,
                 "delete": sites_svc.delete}[action](db, site)
    except Exception:
        db.rollback()
        print(f"[admin] site action {action} on {site.slug} failed:\n"
              + traceback.format_exc())
        return redirect("/admin?m=publish_failed")
    return redirect(f"/admin?m={flash}")


# ---------- توکن‌های عامل اکوسیستم (Cycle 5 — pat_) ----------

@router.get("/admin/agent-tokens")
def agent_tokens_page(request: Request, user=Depends(admin_guard),
                      db: Session = Depends(get_db)):
    """صفحهٔ توکن عامل اکوسیستم — ساخت/ابطال pat_ (قرارداد TICKETING §۶).

    مقدار خام توکن هرگز در دیتابیس نیست؛ فقط هش sha256. نمایش کامل فقط
    یک‌بار، در پاسخِ خودِ فرم ساخت (new_token) — نه در ریدایرکت (URL لاگ می‌شود).
    """
    from ..models import AgentPatToken, EcosystemAgent
    from ..services import agents as asvc
    asvc.ensure_default_agent(db)
    agents_rows = db.query(EcosystemAgent).order_by(EcosystemAgent.id).all()
    tokens = (db.query(AgentPatToken)
              .order_by(AgentPatToken.id.desc()).limit(100).all())
    return render("admin_agent_tokens.html",
                  ctx(request, user, agents=agents_rows, tokens=tokens,
                      new_token=None, now=db_now()))


@router.post("/admin/agent-tokens/new")
def agent_token_new(request: Request, name: str = Form(""),
                    days: str = Form(""),
                    user=Depends(admin_guard), db: Session = Depends(get_db)):
    """ساخت توکن تازه برای عضو «پرستو» (مرکز فرماندهی) — نمایش یک‌بار."""
    from ..models import AgentPatToken, EcosystemAgent
    from ..services import agents as asvc
    agent = asvc.ensure_default_agent(db)
    if not asvc.name_ok(name):
        return redirect("/admin/agent-tokens?m=agent_token_bad_name")
    days_n = None
    if (days or "").strip():
        try:
            days_n = max(1, min(int(days.strip()), 365))
        except ValueError:
            return redirect("/admin/agent-tokens?m=agent_token_bad_days")
    raw, _row = asvc.mint(db, agent, name, days_n)
    agents_rows = db.query(EcosystemAgent).order_by(EcosystemAgent.id).all()
    tokens = (db.query(AgentPatToken)
              .order_by(AgentPatToken.id.desc()).limit(100).all())
    return render("admin_agent_tokens.html",
                  ctx(request, user, agents=agents_rows, tokens=tokens,
                      new_token=raw, now=db_now()))


@router.post("/admin/agent-tokens/{token_id}/revoke")
def agent_token_revoke(request: Request, token_id: int,
                       user=Depends(admin_guard),
                       db: Session = Depends(get_db)):
    """ابطال توکن عامل — فوری و قطعی (قلمرو مستقل از توکن پل و ادمین)."""
    from ..models import AgentPatToken
    row = db.get(AgentPatToken, token_id)
    if row is None:
        return redirect("/admin/agent-tokens?m=not_found")
    if row.revoked_at is None:
        row.revoked_at = db_now()
        db.commit()
    return redirect("/admin/agent-tokens?m=agent_token_revoked")
