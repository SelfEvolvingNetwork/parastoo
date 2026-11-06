"""ورکرهای پس‌زمینه پرستو (هرکدام یک ترد ساده — مناسب تک‌سرور):

1) bootstrap_loop : اتصال اولیه به گیتا (ساخت ادمین گیتا + توکن سیستمی) و
   ساخت مخزن برای پروژه‌های در انتظار — با تلاش مجدد خودکار.
2) build_worker   : صف بیلد (وب‌هوک push → کلون → بیلد → push به رجیستری → دیپلوی خودکار).
3) billing_loop   : کسر هزینه ساعتی منابع از کیف پول و توقف سایت‌های بدحساب
   (منطق کسر از v0.3.0 در services/billing.py است؛ اینجا فقط زمان‌بندی و نشست).
4) domain_loop    : فعال‌سازی خودگردان دامنهٔ زمان‌بندی‌شده (v0.3.14) — هر ۶۰
   ثانیه چک DNS برای درخواست معلق ویزارد؛ پس از نشستن رکوردها همان مسیر
   apply_domain اجرا می‌شود (مالک نیازی به بازگشت به ویزارد ندارد).
5) heal_loop      : خودترمیم‌های بی‌سر و صدا (هر ۱۲۰ ثانیه):
   a) ناهم‌خوانی گیتا با دامنهٔ فعال (v0.3.22) — نصب‌هایی که دامنه‌شان عوض
      شده ولی env گیتا روی نصب اول مانده (میدان 2026-11-06: realm کهنهٔ
      رجیستری → 404 روی docker login → هیچ بیلدی سبز نمی‌شد) بدون دخالت
      مالک بازسازی می‌شوند.
   b) گواهی جاافتادهٔ سایت‌ها (v0.3.23) — ترافیک پس از یک شکست ACME دیگر
      خودش تلاش نمی‌کند؛ اگر DNS درست نشسته باشد ولی گواهی سایتی نباشد
      (میدان همان روز: my-shop.apps… ساعت‌ها روی HTTPS باز نمی‌شد در حالی
      که پنل «running» می‌گفت)، ترافیک با فاصلهٔ حداقل ۱۰ دقیقه ری‌استارت
      می‌شود تا چرخهٔ صدور از نو اجرا شود.
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
HEAL_INTERVAL = 120           # ثانیه بین چک‌های خودترمیم گیتا (v0.3.22)
TLS_RESTART_GAP = 600         # حداقل فاصلهٔ ری‌استارت ترافیک برای تلاش مجدد ACME (v0.3.23)

_last_tls_restart = 0.0  # زمان آخرین ری‌استارت ترافیک در این فرایند


def start_workers() -> None:
    if settings.DISABLE_WORKERS:
        print("[parastoo] workers disabled (PARASTOO_DISABLE_WORKERS=1)")
        return
    threading.Thread(target=_loop, args=(bootstrap_once, 15), daemon=True, name="bootstrap").start()
    threading.Thread(target=_loop, args=(build_once, 5), daemon=True, name="builder").start()
    threading.Thread(target=_loop, args=(billing_once, 60), daemon=True, name="billing").start()
    threading.Thread(target=_loop, args=(domain_once, DOMAIN_INTERVAL), daemon=True, name="domain").start()
    threading.Thread(target=_loop, args=(heal_once, HEAL_INTERVAL), daemon=True, name="heal").start()
    print("[parastoo] workers started (bootstrap / builder / billing / domain / heal)")


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


# ---------------------------------------------------------------- heal

def heal_once() -> None:
    """خودترمیم‌های بی‌سر و صدا — هر ۱۲۰ ثانیه.

    الف) گیتا (v0.3.22): برای نصب‌هایی که دامنه‌شان تغییر کرده و env گیتا
    هنوز دامنهٔ نصب اول است: چالش رجیستری داکر (Www-Authenticate realm) از
    ROOT_URL گیتا می‌آید؛ با دامنهٔ کهنه، docker login دنبال realm کهنه
    می‌رود، ترافیک روتری برای آن Host ندارد و هر بیلد در مرحلهٔ push به
    «404 page not found» می‌میرد. بازسازی کانتینر گیتا
    (dockerops.recreate_gitea) و همگام‌سازی .env هاست (sync_host_env — تا یک
    up بعدی compose مقدار کهنه را پس نزند) هر دو best-effort است؛ شکست فقط
    لاگ می‌شود و سیکل بعدی دوباره تلاش می‌کند.

    ب) گواهی سایت‌ها (v0.3.23): اگر صدور گواهی یک سایت در لحظهٔ انتشار به
    SERVFAIL گذرای DNS بخورد، ترافیک دیگر هرگز خودش تلاش نمی‌کند و سایت
    بی‌گواهی می‌ماند در حالی که پنل «running» می‌گوید. اینجا فقط وقتی که
    DNS عمومی زیردامنه درست نشسته ولی گواهی معتبر نیست، ترافیک ری‌استارت
    می‌شود (با فاصلهٔ حداقل ۱۰ دقیقه) تا چرخهٔ ACME از نو اجرا شود؛ گارد DNS
    جلوی ری‌استارت بی‌پایان برای دامنه‌هایی که اصلاً به سرور اشاره نمی‌کنند
    را می‌گیرد.
    """
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.is_admin.is_(True)).first():
            return  # ویزارد هنوز کامل نشده — دامنهٔ فعلی هنوز معنا ندارد
    finally:
        db.close()
    if not dockerops.available():
        return
    try:
        stale, cur = dockerops.gitea_stale()
    except Exception:
        return
    if stale:
        print(f"[parastoo] gitea ROOT_URL stale ({cur or 'unset'}) — recreating "
              f"with active domain {dockerops.gitea_root_url()}")
        try:
            dockerops.recreate_gitea()
            env_ok = dockerops.sync_host_env()
            print(f"[parastoo] gitea recreated with active domain "
                  f"(host .env synced={env_ok})")
        except Exception as e:
            print(f"[parastoo] gitea self-heal failed: {e}")
    _tls_certs_heal(db)


def _tls_certs_heal(db) -> None:
    """فاز ب) خودترمیم گواهی — جزئیات در heal_once (v0.3.23). best-effort:
    هر استثنایی فقط لاگ می‌شود و هرگز بقیهٔ چرخهٔ heal را نمی‌بندد."""
    global _last_tls_restart
    try:
        if not domains.tls(db):
            return  # TLS خاموش است — گواهی در کار نیست
        ip = domains.server_ip(db)
        if not ip:
            return
        broken = []
        for site in db.query(Site).filter(Site.status == "running").all():
            if traefik_ops.resolve_a(site.subdomain) != ip:
                continue  # DNS هنوز به این سرور اشاره ندارد — دست نزنیم
            if traefik_ops.site_cert_ok(site.subdomain) is False:
                broken.append(site.subdomain)
        if not broken or time.time() - _last_tls_restart < TLS_RESTART_GAP:
            return
        _last_tls_restart = time.time()
        print(f"[parastoo] cert missing for {', '.join(broken)} — "
              f"restarting traefik to retry ACME")
        c = dockerops.client()
        c.containers.get(settings.TRAEFIK_CONTAINER).restart(timeout=10)
    except Exception as e:
        print(f"[parastoo] cert self-heal failed: {e}")


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
        # v0.3.16 — نظافت دوره‌ای پوشهٔ داینامیک: فایل پنهانِ جا مانده
        # کل فایل‌پروایدر ترافیک را می‌شکند (درس میدانی؛ LESSONS)
        for gone in traefik_ops.clean_dynamic_dir():
            print(f"[parastoo] dynamic dir: removed legacy {gone}")
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
