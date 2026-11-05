"""ورکرهای پس‌زمینه پرستو (هرکدام یک ترد ساده — مناسب تک‌سرور):

1) bootstrap_loop : اتصال اولیه به گیتا (ساخت ادمین گیتا + توکن سیستمی) و
   ساخت مخزن برای پروژه‌های در انتظار — با تلاش مجدد خودکار.
2) build_worker   : صف بیلد (وب‌هوک push → کلون → بیلد → push به رجیستری → دیپلوی خودکار).
3) billing_loop   : کسر هزینه ساعتی منابع از کیف پول و توقف سایت‌های بدحساب
   (منطق کسر از v0.3.0 در services/billing.py است؛ اینجا فقط زمان‌بندی و نشست).
4) domain_loop    : فعال‌سازی خودگردان دامنهٔ زمان‌بندی‌شده (v0.3.14) — هر ۶۰
   ثانیه چک DNS برای درخواست معلق ویزارد؛ پس از نشستن رکوردها همان مسیر
   apply_domain اجرا می‌شود (مالک نیازی به بازگشت به ویزارد ندارد).
"""
import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import dockerops, domains, gitea, stacks, traefik_ops
from .config import settings
from .db import SessionLocal, db_now
from .models import Build, Project, Setting, Site, User
from .services import billing
from .services import projects as projects_svc

BUILD_ROOT = Path(settings.DATA_DIR) / "builds"
LOG_TAIL = 60_000  # حداکثر کاراکترهای نگه‌داری‌شده در لاگ بیلد
DOMAIN_INTERVAL = 60          # ثانیه بین چک‌های DNS
PENDING_DEADLINE_S = 72 * 3600  # سقف تلاش برای فعال‌سازی معلق (۷۲ ساعت)


def start_workers() -> None:
    if settings.DISABLE_WORKERS:
        print("[parastoo] workers disabled (PARASTOO_DISABLE_WORKERS=1)")
        return
    threading.Thread(target=_loop, args=(bootstrap_once, 15), daemon=True, name="bootstrap").start()
    threading.Thread(target=_loop, args=(build_once, 5), daemon=True, name="builder").start()
    threading.Thread(target=_loop, args=(billing_once, 60), daemon=True, name="billing").start()
    threading.Thread(target=_loop, args=(domain_once, DOMAIN_INTERVAL), daemon=True, name="domain").start()
    print("[parastoo] workers started (bootstrap / builder / billing / domain)")


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
            # اسکفولد استاندارد (v0.3.2) — best-effort؛ حتی اگر نشد مخزن خالی می‌ماند
            projects_svc.seed_scaffold(project, owner)
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
            # پیش‌بازرسی استک (v0.3.2): پیام فارسی گویا به‌جای خطای مبهم داکر
            # (حادثهٔ ppp: استک node ولی مخزن بدون package.json → COPY failed)
            marker_err = stacks.missing_marker(project.stack, build_dir)
            if marker_err:
                raise RuntimeError(marker_err)
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
    """یک دور صورتحساب با نشست تازه — منطق کسر/توقف در services/billing.py."""
    db = SessionLocal()
    try:
        billing.charge_running_sites(db)
    finally:
        db.close()


# ---------------------------------------------------------------- domain

def domain_once() -> None:
    """یک دور فعال‌سازی خودگردان دامنهٔ معلق (v0.3.14).

    درخواست معلق ویزارد (domains.pending_get) را می‌گیرد؛ اگر رکوردهای DNS
    (پنل/گیت/وایلکارد) به IP سرور نشسته باشند، همان مسیر apply_domain ویزارد
    را کامل اجرا می‌کند و انتظار را برمی‌دارد. سقف ۷۲ ساعت — بعدش پاک می‌شود
    تا ورکر تا ابد دنبال دامنه‌ای که هرگز ننشست نماند.
    """
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.is_admin.is_(True)).first():
            return  # ویزارد هنوز کامل نشده
        p = domains.pending_get(db)
        if not p:
            return
        if p.get("since") and time.time() - p["since"] > PENDING_DEADLINE_S:
            domains.pending_clear(db)
            print(f"[parastoo] pending domain expired (>72h): {p['base']} — cleared")
            return
        records = traefik_ops.check_dns(p["base"], domains.server_ip(db))
        if not all(r["ok"] for r in records):
            return  # DNS هنوز ننشسته — سیکل بعدی
        result = traefik_ops.apply_domain(db, p["base"], p.get("acme") or "",
                                          bool(p.get("tls")))
        domains.pending_clear(db)
        print(f"[parastoo] pending domain activated: {p['base']} "
              f"(tls={bool(p.get('tls'))}, sites={result['sites_updated']}, "
              f"errors={result['errors'] or 'none'})")
    except Exception as e:
        print(f"[parastoo] domain worker error: {e}")
    finally:
        db.close()


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
