"""پروژه‌ها — لایهٔ نازک HTTP: اعتبارسنجی فرم → سرویس → ریدایرکت/رندر.

منطق ساخت/حذف/بیلد در services/projects.py و انتشار سایت در
services/sites.py است. از v0.5.2 فرم ساخت پروژه هیچ فناوری‌ای نمی‌پرسد —
فناوری در بیلد از محتوای مخزن خوانده می‌شود؛ مخزن تازه فقط بذر جهانی
(scaffolds.py) می‌گیرد.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import domains, traefik_ops
from ..db import get_db, db_now
from ..deps import ctx, csrf_guard, redirect, render
from ..models import AgentToken, Build, Project, Site
from ..services import agentpair as agentpair_svc
from ..services import bridge as bridge_svc
from ..services import billing as billing_svc
from ..services import projects as projects_svc
from ..services import sites as sites_svc

router = APIRouter()


@router.get("/projects")
def projects_list(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    items = (db.query(Project)
             .filter(Project.owner_id == user.id)
             .order_by(Project.id.desc())
             .all())
    return render("projects.html", ctx(request, user, items=items))


@router.get("/projects/new")
def project_new_page(request: Request, user=Depends(csrf_guard)):
    return render("project_new.html", ctx(request, user))


@router.post("/projects/new")
def project_create(
    name: str = Form(...),
    slug: str = Form(...),
    description: str = Form(""),
    user=Depends(csrf_guard),
    db: Session = Depends(get_db),
):
    project, flash = projects_svc.create_project(db, user, name, slug, description)
    if project is None:
        return redirect(f"/projects/new?m={flash}")
    return redirect(f"/projects/{project.slug}?m={flash}")


def _project_page(request: Request, user, db: Session, project: Project,
                  new_token: str | None = None):
    """رندر صفحهٔ پروژه — مشترک بین GET و پاسخِ ساخت توکن (نمایش یک‌بار توکن)."""
    builds = (db.query(Build)
              .filter(Build.project_id == project.id)
              .order_by(Build.id.desc())
              .limit(10)
              .all())
    sites = (db.query(Site)
             .filter(Site.project_id == project.id)
             .order_by(Site.id.desc())
             .all())
    services = sorted(project.services, key=lambda s: (not s.is_public, s.name))
    hourly = billing_svc.hourly_cost([s for s in services if s.status == "running"])
    has_repo = not project.source_project_id
    git_ready = has_repo and project.repo_status == "ready" and bool(user.gitea_token)
    tokens = (db.query(AgentToken)
              .filter(AgentToken.project_id == project.id,
                      AgentToken.revoked_at.is_(None))
              .order_by(AgentToken.id.desc())
              .all())
    scheme = "https" if domains.tls() else "http"
    panel_url = f"{scheme}://{domains.panel()}"
    agent_invite = agentpair_svc.invite_text(project.slug, panel_url)
    return render("project_detail.html", ctx(
        request, user, p=project, builds=builds, sites=sites, services=services,
        hourly=hourly, has_repo=has_repo,
        git_ready=git_ready,
        tokens=tokens, new_token=new_token, panel_url=panel_url,
        agent_invite=agent_invite,
    ))


@router.get("/projects/{slug}")
def project_detail(request: Request, slug: str,
                   user=Depends(csrf_guard), db: Session = Depends(get_db)):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    return _project_page(request, user, db, project)


@router.post("/projects/{slug}/agent-tokens")
def agent_token_create(
    request: Request, slug: str,
    name: str = Form(""),
    days: str = Form(""),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    """ساخت توکن پل عامل — مقدار کامل فقط همین یک‌بار در پاسخ رندر می‌شود."""
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    label = (name or "").strip()
    if not bridge_svc.name_ok(label):
        return redirect(f"/projects/{slug}?m=token_name")
    days_n = None
    if (days or "").strip():
        try:
            days_n = int(days)
        except ValueError:
            days_n = None
    raw, _row = bridge_svc.mint(db, project, label, days_n)
    bridge_svc.audit(None, project, "token_created", f"{label} ({_row.prefix}…)",
                     request.client.host if request.client else "")
    return _project_page(request, user, db, project, new_token=raw)


@router.post("/projects/{slug}/agent-approve")
def agent_pairing_approve(
    request: Request, slug: str,
    code: str = Form(""),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    """تأیید انسانی رمز ۵ رقمیِ عامل — فقط برای ادعاهای همین پروژه (v0.5.27).

    همان الگوی پل ادمین: عامل رمز را خودش ساخته و در چت نشان داده است؛
    صاحب پروژه همین‌جا تأییدش می‌کند تا عامل توکن pst_ بگیرد. مقدار رمز
    هرگز ذخیره یا لاگ نمی‌شود — فقط هش."""
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    ip = request.client.host if request.client else ""
    res = agentpair_svc.approve(project.id, code, ip)
    if res.get("ok"):
        agentpair_svc.audit_pair("approved", f"slug={slug} claim_id={res.get('claim_id')}", ip)
        return redirect(f"/projects/{slug}?m=agent_approved")
    agentpair_svc.audit_pair("approve_failed", f"slug={slug} error={res.get('error')}",
                             ip, ok=False)
    return redirect(f"/projects/{slug}?m=agent_approve_failed")


@router.post("/projects/{slug}/agent-tokens/{tid}/revoke")
def agent_token_revoke(
    request: Request, slug: str, tid: int,
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    """ابطال فوری توکن — همهٔ فراخوانی‌های بعدی پل 401 می‌گیرند."""
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    row = (db.query(AgentToken)
           .filter(AgentToken.id == tid, AgentToken.project_id == project.id,
                   AgentToken.revoked_at.is_(None))
           .first())
    if row is not None:
        row.revoked_at = db_now()
        db.commit()
        bridge_svc.audit(None, project, "token_revoked", row.prefix,
                         request.client.host if request.client else "")
    return redirect(f"/projects/{slug}?m=token_revoked")


@router.post("/projects/{slug}/rebuild")
def project_rebuild(request: Request, slug: str,
                    user=Depends(csrf_guard), db: Session = Depends(get_db)):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    flash = projects_svc.queue_build(db, project)
    return redirect(f"/projects/{slug}?m={flash}")


@router.post("/projects/{slug}/delete")
def project_delete(
    request: Request, slug: str,
    confirm: str = Form(""),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    """حذف پروژه: تأیید با تایپ دقیق شناسه؛ آبشار پاکسازی در سرویس."""
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    if confirm.strip() != project.slug:
        return redirect(f"/projects/{slug}?m=confirm_mismatch")
    err = projects_svc.delete_project(db, project, user)
    if err:
        return redirect(f"/projects/{slug}?m={err}")
    return redirect("/projects?m=project_deleted")


@router.post("/projects/{slug}/marketplace")
def project_marketplace(
    request: Request, slug: str,
    is_public: str = Form(""),
    price: str = Form("0"),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    project.is_public = bool(is_public)
    try:
        project.price = max(0, int(price or 0))
    except ValueError:
        project.price = 0
    db.commit()
    return redirect(f"/projects/{slug}?m=mp_updated")


@router.post("/projects/{slug}/publish")
def project_publish(
    request: Request, slug: str,
    site_slug: str = Form(...),
    tier: str = Form("nano"),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    # tier برای پروژه‌های تک-سرویسیِ بدون مانیفست اعمال می‌شود؛ پروژه‌های
    # مانیفست‌دار tier خود را از parastoo.yml دارند
    site, flash = sites_svc.publish_from_project(db, user, project, site_slug, tier)
    if flash:
        return redirect(f"/projects/{slug}?m={flash}")
    # صداقت انتشار (v0.3.23): اگر ترافیک هنوز گواهی HTTPS این سایت را ندارد،
    # همین‌جا بگوییم — نباید «منتشر شد» سبزی بدهیم و سایت روی HTTPS باز نشود
    # (میدان 2026-11-06: شکست ACME به‌خاطر SERVFAIL گذرا، ساعت‌ها پنهان ماند).
    # ورکر heal خودش تلاش مجدد می‌کند؛ کاربر فقط باید بداند چند دقیقه‌ای طول می‌کشد.
    if site is not None and domains.tls() and \
            traefik_ops.site_cert_ok(site.subdomain) is False:
        return redirect(f"/projects/{slug}?m=published_cert_pending")
    return redirect(f"/projects/{slug}?m=published")
