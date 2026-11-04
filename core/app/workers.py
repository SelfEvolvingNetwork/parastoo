"""ورکرهای پس‌زمینه پرستو (هرکدام یک ترد ساده — مناسب تک‌سرور):

1) bootstrap_loop : اتصال اولیه به گیتا (ساخت ادمین گیتا + توکن سیستمی) و
   ساخت مخزن برای پروژه‌های در انتظار — با تلاش مجدد خودکار.
2) build_worker   : صف بیلد (وب‌هوک push → کلون → بیلد → push به رجیستری → دیپلوی خودکار).
3) billing_loop   : کسر هزینه ساعتی منابع از کیف پول و توقف سایت‌های بدحساب.
"""
import subprocess
import threading
import time
import traceback
from datetime import timedelta
from pathlib import Path

from . import dockerops, gitea, stacks
from .config import settings
from .db import SessionLocal, db_now
from .models import Build, Project, Setting, Site, User, WalletTx

BUILD_ROOT = Path(settings.DATA_DIR) / "builds"
LOG_TAIL = 60_000  # حداکثر کاراکترهای نگه‌داری‌شده در لاگ بیلد


def start_workers() -> None:
    if settings.DISABLE_WORKERS:
        print("[parastoo] workers disabled (PARASTOO_DISABLE_WORKERS=1)")
        return
    threading.Thread(target=_loop, args=(bootstrap_once, 15), daemon=True, name="bootstrap").start()
    threading.Thread(target=_loop, args=(build_once, 5), daemon=True, name="builder").start()
    threading.Thread(target=_loop, args=(billing_once, 60), daemon=True, name="billing").start()
    print("[parastoo] workers started (bootstrap / builder / billing)")


def _loop(fn, interval: int) -> None:
    while True:
        try:
            fn()
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


# ---------------------------------------------------------------- bootstrap

def bootstrap_once() -> None:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.is_admin.is_(True)).first():
            return  # هنوز ویزارد تکمیل نشده
        if db.get(Setting, "gitea_system_token"):
            _sync_pending(db)
            return
        if not gitea.GiteaClient().healthy():
            return
        print("[parastoo] bootstrapping gitea...")
        gitea.bootstrap_system_admin()
        gitea.get_system_token()
        print("[parastoo] gitea bootstrap done")
        _sync_pending(db)
    finally:
        db.close()


def _sync_pending(db) -> None:
    """خودترمیمی: ساخت حساب گیت کاربران و مخزن پروژه‌های در انتظار."""
    token = gitea.system_token(db)
    if not token:
        return
    for user in db.query(User).filter(User.is_active.is_(True)).all():
        if not user.gitea_username or not user.gitea_token:
            try:
                gitea.provision_user_git(user)
                db.commit()
                print(f"[parastoo] git account ready for user {user.email}")
            except Exception as e:
                db.rollback()
                print(f"[parastoo] git provision failed for {user.email}: {e}")

    for project in db.query(Project).filter(Project.repo_status == "pending").all():
        owner = project.owner
        if not owner.gitea_username:
            continue
        try:
            gitea.provision_project_repo(project, owner)
            project.repo_status = "ready"
            db.commit()
            print(f"[parastoo] repo ready: {owner.gitea_username}/{project.slug}")
        except Exception as e:
            db.rollback()
            # در حالت pending می‌ماند تا در سیکل بعد دوباره تلاش شود (خودترمیمی)
            print(f"[parastoo] repo provision retry for {project.slug}: {e}")


# ---------------------------------------------------------------- build

def build_once() -> None:
    db = SessionLocal()
    try:
        job = (db.query(Build)
               .filter(Build.status == "queued")
               .order_by(Build.id.asc())
               .first())
        if not job:
            return
        job.status = "running"
        db.commit()
        _run_build(db, job)
    finally:
        db.close()


def _log_fn(job, db):
    def log(line: str) -> None:
        print(f"[build:{job.id}] {line}")
        try:
            job.log = (job.log or "")[-LOG_TAIL:] + line + "\n"
            db.commit()
        except Exception:
            db.rollback()
    return log


def _run_build(db, job: Build) -> None:
    project = db.get(Project, job.project_id)
    owner = project.owner
    log = _log_fn(job, db)

    if not owner.gitea_token or project.repo_status != "ready":
        job.status = "failed"
        job.finished_at = db_now()
        job.log = "مخزن گیت آماده نیست. چند لحظه بعد دوباره push کنید."
        db.commit()
        return

    build_dir = BUILD_ROOT / str(job.id)
    try:
        BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        url = gitea.clone_url_internal(owner).rstrip("/") + f"/{project.slug}.git"
        log(f"[git] clone {owner.gitea_username}/{project.slug} @ {job.commit_sha[:7] or 'HEAD'}")
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(build_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            raise RuntimeError(f"clone failed: {r.stderr[:500]}")
        if job.commit_sha:
            subprocess.run(["git", "checkout", job.commit_sha], cwd=build_dir,
                           capture_output=True, timeout=60)

        # داکرفایل: فایل کاربر یا قالب پرستو
        dockerfile = build_dir / "Dockerfile"
        if stacks.wants_own_dockerfile(project.stack):
            if not dockerfile.exists():
                raise RuntimeError("برای فناوری «داکرفایل اختصاصی» باید فایل Dockerfile در ریشه مخزن باشد")
        else:
            content = stacks.dockerfile_for(project.stack, project.start_cmd)
            if not dockerfile.exists() and content:
                dockerfile.write_text(content)
                log(f"[build] Dockerfile قالبی برای {project.stack} ساخته شد")
        (build_dir / ".dockerignore").write_text(stacks.DOCKERIGNORE)

        image_tag = dockerops.build_and_push(
            build_dir=build_dir,
            owner=owner.gitea_username,
            slug=project.slug,
            sha=job.commit_sha,
            docker_username=owner.gitea_username,
            docker_password=owner.gitea_token,
            log=log,
        )

        job.image_tag = image_tag
        project.latest_image = image_tag
        project.latest_sha = job.commit_sha
        project.build_count += 1

        # به‌روزرسانی خودکار همه سایت‌های وابسته به این پروژه
        for site in db.query(Site).filter(Site.project_id == project.id).all():
            try:
                site.image = image_tag
                dockerops.recreate_site(site)
                site.status = "running"
                log(f"[deploy] سایت {site.slug} با ایمیج جدید به‌روزرسانی شد")
            except Exception as e:
                site.status = "error"
                log(f"[deploy] به‌روزرسانی {site.slug} ناموفق: {e}")

        job.status = "success"
        log("[done] بیلد موفق")
    except Exception as e:
        job.status = "failed"
        log(f"[failed] {e}")
    finally:
        job.finished_at = db_now()
        db.commit()
        dockerops.cleanup_build_dir(build_dir)


# ---------------------------------------------------------------- billing

def billing_once() -> None:
    """هر ساعت، به ازای هر سایت در حال اجرا، هزینه پلن منابع کسر می‌شود."""
    db = SessionLocal()
    try:
        now = db_now()
        sites = (db.query(Site)
                 .filter(Site.status == "running")
                 .all())
        for site in sites:
            tier = settings.TIERS.get(site.tier, settings.TIERS["nano"])
            if site.last_charged_at:
                elapsed = (now - site.last_charged_at).total_seconds()
                if elapsed < 3600:
                    continue
            user = site.owner
            amount = tier["price"]
            # اگر این سایت خودِ پروژه را نشر می‌دهد و کاربر موجودی ندارد → توقف
            user.balance -= amount
            db.add(WalletTx(user_id=user.id, amount=-amount, kind="charge",
                            note=f"هزینه منابع سایت {site.slug} ({tier['label']})"))
            site.last_charged_at = now
            if user.balance < settings.DEBT_LIMIT:
                _pause_site(db, site, user)
        db.commit()
    finally:
        db.close()


def _pause_site(db, site: Site, user: User) -> None:
    try:
        dockerops.stop_site(site.slug)
        site.status = "paused"
        print(f"[billing] site {site.slug} paused (balance {user.balance})")
    except Exception as e:
        print(f"[billing] stop {site.slug} failed: {e}")
        site.status = "error"


# ---------------------------------------------------------------- helpers

def enqueue_build(project_id: int, sha: str) -> int | None:
    db = SessionLocal()
    try:
        job = Build(project_id=project_id, commit_sha=sha or "", status="queued")
        db.add(job)
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()
