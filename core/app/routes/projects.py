"""پروژه‌ها — لایهٔ نازک HTTP: اعتبارسنجی فرم → سرویس → ریدایرکت/رندر.

منطق ساخت/حذف/بیلد در services/projects.py و انتشار سایت در
services/sites.py است.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import domains, traefik_ops
from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render
from ..models import Build, Project, Site
from ..services import projects as projects_svc
from ..services import sites as sites_svc
from ..stacks import default_port

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
    stack: str = Form(...),
    start_cmd: str = Form(""),
    description: str = Form(""),
    user=Depends(csrf_guard),
    db: Session = Depends(get_db),
):
    project, flash = projects_svc.create_project(
        db, user, name, slug, stack, start_cmd, description)
    if project is None:
        return redirect(f"/projects/new?m={flash}")
    return redirect(f"/projects/{project.slug}?m={flash}")


@router.get("/projects/{slug}")
def project_detail(request: Request, slug: str,
                   user=Depends(csrf_guard), db: Session = Depends(get_db)):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    builds = (db.query(Build)
              .filter(Build.project_id == project.id)
              .order_by(Build.id.desc())
              .limit(10)
              .all())
    sites = (db.query(Site)
             .filter(Site.project_id == project.id)
             .order_by(Site.id.desc())
             .all())
    git_ready = project.repo_status == "ready" and bool(user.gitea_token)
    clone_url = f"{domains.git()}/{user.gitea_username}/{project.slug}.git" if git_ready else ""
    return render("project_detail.html", ctx(
        request, user, p=project, builds=builds, sites=sites,
        git_ready=git_ready, clone_url=clone_url,
        default_port=default_port(project.stack),
    ))


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
    port: str = Form(""),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    project = projects_svc.project_for(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    site, flash = sites_svc.publish_from_project(db, user, project, site_slug, tier, port)
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
