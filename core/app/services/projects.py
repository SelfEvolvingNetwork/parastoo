"""چرخهٔ حیات پروژه: ساخت، یافتن با چک مالکیت، اسکفولد استاندارد، صف بیلد، حذف آبشاری."""
from sqlalchemy.orm import Session

from .. import dockerops, gitea, scaffolds
from ..config import settings
from ..models import Build, Project, Site, User
from ..security import gen_secret, slugify
from ..validation import project_slug_error


def project_for(db: Session, user: User, slug: str) -> Project | None:
    """پروژه اگر مالکش فراخواننده باشد (یا ادمین) — وگرنه None (نه 403؛ IDOR-safe)."""
    p = db.query(Project).filter(Project.slug == slug).first()
    if p is None or (p.owner_id != user.id and not user.is_admin):
        return None
    return p


def create_project(db: Session, owner: User, name: str, slug_raw: str,
                   stack: str, start_cmd: str = "", description: str = ""):
    """ساخت پروژه + تلاش فوری برای مخزن گیت.

    برمی‌گرداند: (project|None, flash_key) — project=None یعنی flash_key خطاست.
    اگر گیتا آماده نباشد پروژه ساخته می‌شود (repo_status=pending) و ورکر
    bootstrap بعداً مخزن را می‌سازد (خودترمیمی) — flash: git_pending.
    """
    name = name.strip()
    slug = slugify(slug_raw)
    start_cmd = (start_cmd or "").strip()
    # مدل پایتون از v0.3.2 با FastAPI متولد می‌شود؛ ورکر sync گورنیکون (پیش‌فرض
    # قدیمی) با ASGI نمی‌سازد — پس دستور اجرای پیش‌فرض، ورکر uvicorn است.
    if stack == "python" and not start_cmd:
        start_cmd = scaffolds.DEFAULT_START_CMD["python"]
    err = project_slug_error(slug)
    if err is None and (not name or stack not in settings.STACKS):
        err = "slug_invalid"
    if err:
        return None, err
    if db.query(Project).filter(Project.slug == slug).first():
        return None, "slug_taken"

    project = Project(
        owner_id=owner.id,
        name=name,
        slug=slug,
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
    try:
        gitea.provision_project_repo(project, owner)
        project.repo_status = "ready"
        db.commit()
    except Exception:
        db.rollback()
        flash = "git_pending"
        return project, flash
    seed_scaffold(project, owner)  # best-effort — هرگز ساخت پروژه را نمی‌شکند
    return project, flash


def seed_scaffold(project: Project, owner: User) -> bool:
    """کامیت اولیهٔ مدل استاندارد روی مخزن خالی (v0.3.2) — best-effort.

    قواعد در scaffolds.py: فقط مخزن خالی، هرگز بازنویسی محتوای کاربر،
    شکست فقط لاگ می‌شود (مسیر قدیمی «مخزن خالی» همچنان کار می‌کند).
    """
    try:
        from ..domains import git as git_domain
        clone_url = f"{git_domain()}/{owner.gitea_username}/{project.slug}.git"
        files = scaffolds.files_for(project, clone_url=clone_url)
        if not files:
            return False
        seeded = gitea.seed_project_scaffold(project, owner, files, scaffolds.COMMIT_MESSAGE)
        if seeded:
            print(f"[parastoo] scaffold seeded: {owner.gitea_username}/{project.slug} ({project.stack})")
        return seeded
    except Exception as e:
        print(f"[parastoo] scaffold seed failed for {project.slug}: {e}")
        return False


def queue_build(db: Session, project: Project) -> str:
    """قرار دادن بیلد دستی در صف."""
    db.add(Build(project_id=project.id, commit_sha=project.latest_sha or "",
                 status="queued"))
    db.commit()
    return "build_queued"


def delete_project(db: Session, project: Project, acting_user: User) -> str | None:
    """حذف کامل پروژه: سایت‌های خود مالک + بیلدها + مخزن گیت (best-effort).

    قواعد حفاظتی:
    - اگر سایتی از این پروژه متعلق به کاربر دیگری باشد (خریدار مارکت‌پلیس)،
      حذف مسدود می‌شود تا سایت زندهٔ دیگران نشکند — مالک باید صبر کند تا
      خریداران سایتشان را حذف کنند؛ کلید خطا: project_in_use.
    - تأیید با تایپ شناسهٔ پروژه در مسیر (route) چک می‌شود، نه اینجا.
    """
    referencing = db.query(Site).filter(Site.project_id == project.id).all()
    if any(s.owner_id != acting_user.id for s in referencing):
        return "project_in_use"

    # ۱) سایت‌های خود مالک را از داکر و دیتابیس پاک کن (best-effort در داکر)
    for s in referencing:
        try:
            dockerops.remove_site(s.slug)
        except Exception:
            pass
        db.delete(s)

    # ۲) بیلدها و پروژه
    db.query(Build).filter(Build.project_id == project.id).delete()

    # ۳) مخزن گیت (best-effort — نبود گیتا مانع حذف پروژه نمی‌شود)
    owner = db.get(User, project.owner_id)
    if owner:
        gitea.delete_project_repo(project, owner)

    db.delete(project)
    db.commit()
    return None
