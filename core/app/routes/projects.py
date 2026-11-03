"""پروژه‌ها: ساخت، جزئیات، بیلد دستی، مارکت‌پلیس، انتشار سایت."""
import re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import dockerops, domains, gitea, stacks
from ..config import settings
from ..deps import ctx, csrf_guard, redirect, render
from ..db import get_db
from ..models import Build, Project, Site
from ..security import gen_secret, slugify
from ..tpl import FLASHES

router = APIRouter()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")


def _get_project(db: Session, user, slug: str) -> Project | None:
    p = db.query(Project).filter(Project.slug == slug).first()
    if p is None or (p.owner_id != user.id and not user.is_admin):
        return None
    return p


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
    name = name.strip()
    project_slug = slugify(slug)
    if not name or not SLUG_RE.match(project_slug) or stack not in settings.STACKS:
        return redirect("/projects/new?m=slug_invalid")
    if project_slug in settings.RESERVED_SLUGS:
        return redirect("/projects/new?m=slug_reserved")
    if db.query(Project).filter(Project.slug == project_slug).first():
        return redirect("/projects/new?m=slug_taken")

    project = Project(
        owner_id=user.id,
        name=name,
        slug=project_slug,
        stack=stack,
        start_cmd=start_cmd.strip() or None,
        description=description.strip(),
        webhook_secret=gen_secret(16),
        repo_status="pending",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    flash = "project_created"
    # تلاش فوری برای ساخت مخزن؛ اگر گیتا آماده نیست ورکر بعداً انجام می‌دهد
    try:
        gitea.provision_project_repo(project, user)
        project.repo_status = "ready"
        db.commit()
    except Exception:
        db.rollback()
        flash = "git_pending"
    return redirect(f"/projects/{project_slug}?m={flash}")


@router.get("/projects/{slug}")
def project_detail(request: Request, slug: str,
                   user=Depends(csrf_guard), db: Session = Depends(get_db)):
    project = _get_project(db, user, slug)
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
        default_port=stacks.default_port(project.stack),
    ))


@router.post("/projects/{slug}/rebuild")
def project_rebuild(request: Request, slug: str,
                    user=Depends(csrf_guard), db: Session = Depends(get_db)):
    project = _get_project(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    job = Build(project_id=project.id, commit_sha=project.latest_sha or "", status="queued")
    db.add(job)
    db.commit()
    return redirect(f"/projects/{slug}?m=build_queued")


@router.post("/projects/{slug}/marketplace")
def project_marketplace(
    request: Request, slug: str,
    is_public: str = Form(""),
    price: str = Form("0"),
    user=Depends(csrf_guard), db: Session = Depends(get_db),
):
    project = _get_project(db, user, slug)
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
    project = _get_project(db, user, slug)
    if project is None:
        return redirect("/projects?m=not_found")
    if not project.latest_image:
        return redirect(f"/projects/{slug}?m=image_missing")

    s = slugify(site_slug)
    if not SLUG_RE.match(s) or s in settings.RESERVED_SLUGS:
        return redirect(f"/projects/{slug}?m=slug_invalid")
    if db.query(Site).filter(Site.slug == s).first():
        return redirect(f"/projects/{slug}?m=slug_taken")
    if tier not in settings.TIERS:
        tier = "nano"

    internal_port = stacks.default_port(project.stack)
    if port.strip().isdigit():
        internal_port = max(1, min(65535, int(port)))

    subdomain = f"{s}.{domains.apps()}"
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

    # اجرای کانتینر؛ خرابی داکر باعث از دست رفتن رکورد نمی‌شود
    try:
        dockerops.run_site(
            image=site.image, slug=site.slug, subdomain=site.subdomain,
            port=site.internal_port, tier=site.tier,
        )
    except Exception:
        site.status = "error"
        db.commit()
        return redirect(f"/projects/{slug}?m=publish_failed")
    return redirect(f"/projects/{slug}?m=published")
