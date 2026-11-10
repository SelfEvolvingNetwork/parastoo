"""ورکرهای پس‌زمینه پرستو (هرکدام یک ترد ساده — مناسب تک‌سرور):

1) bootstrap_loop : اتصال اولیه به گیتا (ساخت ادمین گیتا + توکن سیستمی) و
   ساخت مخزن برای پروژه‌های در انتظار — با تلاش مجدد خودکار.
2) build_worker   : صف بیلد (وب‌هوک push → کلون → بیلد → push به رجیستری → دیپلوی خودکار).
3) billing_loop   : کسر هزینه ساعتی منابع از کیف پول و توقف سایت‌های بدحساب
   (منطق کسر از v0.3.0 در services/billing.py است؛ اینجا فقط زمان‌بندی و نشست).
4) domain_loop    : فعال‌سازی خودگردان دامنهٔ زمان‌بندی‌شده (v0.3.14) — هر ۶۰
   ثانیه چک DNS برای درخواست معلق ویزارد؛ پس از نشستن رکوردها همان مسیر
   apply_domain اجرا می‌شود (مالک نیازی به بازگشت به ویزارد ندارد).
5) heal_loop      : خودترمیم‌های شفاف (هر ۱۲۰ ثانیه — هر اقدام با _heal_log در
   stdout و رینگ‌بافر devlog دیده می‌شود):
   a) ناهم‌خوانی گیتا با دامنهٔ فعال (v0.3.22) — نصب‌هایی که دامنه‌شان عوض
      شده ولی env گیتا روی نصب اول مانده (میدان 2026-11-06: realm کهنهٔ
      رجیستری → 404 روی docker login → هیچ بیلدی سبز نمی‌شد) بدون دخالت
      مالک بازسازی می‌شوند.
   b) گواهی جاافتادهٔ سایت‌ها (v0.3.23) — ترافیک پس از یک شکست ACME دیگر
      خودش تلاش نمی‌کند؛ اگر DNS درست نشسته باشد ولی گواهی سایتی نباشد
      (میدان همان روز: my-shop.apps… ساعت‌ها روی HTTPS باز نمی‌شد در حالی
      که پنل «running» می‌گفت)، ترافیک با فاصلهٔ حداقل ۱۰ دقیقه ری‌استارت
      می‌شود تا چرخهٔ صدور از نو اجرا شود.
6) mobile_loop    : انتشار خودکار «پرستو موبایل» (v0.5.9) — هر ۱۲۰ ثانیه
   آخرین رلیز گیت‌هاب مخزن اپ ادمین را می‌پرسد؛ بیلد APK تازه را بی‌صدا
   دانلود و در /data/mobile نصب می‌کند تا صفحهٔ /download همیشه آخرین
   نسخهٔ نصب‌شدنی را بدهد (services/mobile.py — صفر رمز، بهترین‌تلاش).
"""
import logging
import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import dockerops, domains, gitea, manifest, stacks, traefik_ops
from .config import settings
from .db import SessionLocal, db_now
from .models import Build, Project, Service, Setting, Site, User
from .services import billing
from .services import business as business_svc
from .services import mobile
from .services import projects as projects_svc
from .services import sites as sites_svc

BUILD_ROOT = Path(settings.DATA_DIR) / "builds"
LOG_TAIL = 60_000  # حداکثر کاراکترهای نگه‌داری‌شده در لاگ بیلد
DOMAIN_INTERVAL = 60          # ثانیه بین چک‌های DNS
PENDING_DEADLINE_S = 72 * 3600  # سقف تلاش برای فعال‌سازی معلق (۷۲ ساعت)
HEAL_INTERVAL = 120           # ثانیه بین چک‌های خودترمیم گیتا (v0.3.22)
MOBILE_INTERVAL = 120         # ثانیه بین چک‌های رلیز پرستو موبایل (v0.5.11؛ قبلاً ۶۰۰ — مالک: «چرا انتشار اینقدر طول کشید؟» ۳۰ کوئری/ساعت، خیلی زیر سقف ۶۰/ساعت گیت‌هاب)
TLS_RESTART_GAP = 600         # حداقل فاصلهٔ ری‌استارت ترافیک برای تلاش مجدد ACME (v0.3.23)
REGISTRY_RETRY_GAP = 900      # حداقل فاصلهٔ صف‌کردن مجدد بیلد مردود رجیستری (v0.3.29)

_last_tls_restart = 0.0  # زمان آخرین ری‌استارت ترافیک در این فرایند
_last_registry_retry = 0.0  # زمان آخرین صف‌کردن مجدد بیلد در این فرایند


def start_workers() -> None:
    if settings.DISABLE_WORKERS:
        print("[parastoo] workers disabled (PARASTOO_DISABLE_WORKERS=1)")
        return
    threading.Thread(target=_loop, args=(bootstrap_once, 15), daemon=True, name="bootstrap").start()
    threading.Thread(target=_loop, args=(build_once, 5), daemon=True, name="builder").start()
    threading.Thread(target=_loop, args=(billing_once, 60), daemon=True, name="billing").start()
    threading.Thread(target=_loop, args=(domain_once, DOMAIN_INTERVAL), daemon=True, name="domain").start()
    threading.Thread(target=_loop, args=(heal_once, HEAL_INTERVAL), daemon=True, name="heal").start()
    threading.Thread(target=_loop, args=(mobile_once, MOBILE_INTERVAL), daemon=True, name="mobile-release").start()
    threading.Thread(target=_loop, args=(business_svc.heartbeat_once, business_svc.HEARTBEAT_INTERVAL), daemon=True, name="heartbeat").start()
    print("[parastoo] workers started (bootstrap / builder / billing / domain / heal / mobile-release / heartbeat)")


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
            gitea.ensure_sso_source(db)   # خودترمیم منبع SSO (v0.5.7) — idempotent
            gitea.ensure_custom_templates_and_restart()   # «بازگشت به پنل» (v0.5.8)
            _sync_pending(db)
            return
        if not gitea.GiteaClient().healthy():
            return
        print("[parastoo] bootstrapping gitea...")
        gitea.bootstrap_system_admin()
        gitea.get_system_token()
        gitea.ensure_sso_source(db)
        gitea.ensure_custom_templates_and_restart()
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
    """بیلد سرویس‌محور (v0.4.0) — کل یا هیچ:

    ۱) مانیفست (parastoo.yml) یا تشخیص خودکار تک‌سرویسی از محتوای مخزن؛
    ۲) بیلد «همهٔ» ایمیج‌ها — اگر یکی مرد، اینجا تمام می‌شود و کانتینرهای
       در حال اجرای پروژه دست‌نخورده می‌مانند (استقرار فقط بعد از سبز شدن کامل)؛
    ۳) همگام‌سازی ردیف‌های Service با مانیفست (upsert + حذف سرویس‌های رفته);
    ۴) استقرار کامل پروژه فقط اگر سایتی دارد (کانتینر بی‌روته هزینهٔ بی‌دلیل است)
       + انتشار به‌روزرسانی به نصب‌های مارکت‌پلیسِ همین اپ.
    """
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

        # ۱) سرویس‌های پروژه: مانیفست یا تشخیص خودکار (محدودیت فناوری وجود ندارد)
        specs, src_kind = manifest.resolve(build_dir)
        if src_kind == "manifest":
            log(f"[manifest] {len(specs)} سرویس از parastoo.yml: "
                + ", ".join(f"{s.name}{'*' if s.is_public else ''}" for s in specs))
        else:
            log(f"[auto] تک‌سرویس خودکار (تشخیص از محتوای مخزن: {manifest.detect_stack(build_dir)})")

        # ۲) بیلد همهٔ ایمیج‌ها — کل یا هیچ. فقط سرویس‌های build-دار بیلد می‌شوند؛
        # سرویس‌های فقط-ایمیج هنگام استقرار از رجیستری pull می‌شوند.
        images: dict[str, str] = {}
        for spec in specs:
            if not spec.build:
                log(f"[image:{spec.name}] بدون بیلد — از ایمیج اعلام‌شده استفاده می‌شود: {spec.image}")
                continue
            ctx = (build_dir / spec.build).resolve() if spec.build != "." else build_dir
            if spec.build and (not ctx.is_dir() or not str(ctx).startswith(str(build_dir.resolve()))):
                raise RuntimeError(f"پوشهٔ بیلد سرویس «{spec.name}» پیدا نشد: {spec.build}")
            has_own = (ctx / "Dockerfile").exists()
            if spec.dockerfile_template and not has_own:
                if spec.marker and not (ctx / spec.marker[0]).exists():
                    raise RuntimeError(spec.marker[1])  # پیش‌بازرسی قالب — پیام فارسی stacks
                (ctx / "Dockerfile").write_text(spec.dockerfile_template, encoding="utf-8")
                log(f"[build:{spec.name}] Dockerfile قالبی ساخته شد")
            (ctx / ".dockerignore").write_text(stacks.DOCKERIGNORE, encoding="utf-8")
            images[spec.name] = dockerops.build_and_push(
                build_dir=ctx,
                owner=owner.gitea_username,
                slug=project.slug,
                svc=spec.name,
                sha=job.commit_sha,
                docker_username=owner.gitea_username,
                docker_password=owner.gitea_token,
                log=log,
            )

        # ۳) همگام‌سازی ردیف‌های Service با سرویس‌های اعلام‌شده
        _sync_services(db, project, specs, images, src_kind)
        project.manifest_json = manifest.specs_to_json(specs)
        project.latest_sha = job.commit_sha

        # ۴) استقرار دانهٔ پروژه — فقط وقتی سایتی دارد (بی‌روته = خاموش و بی‌هزینه)
        if project.sites:
            sites_svc._deploy(db, project)
            log("[deploy] همهٔ سرویس‌های پروژه با ایمیج جدید مستقر شدند")
        else:
            log("[deploy] پروژه سایتی ندارد — ایمیج‌ها آماده‌اند؛ با «انتشار سایت» اجرا می‌شوند")

        _propagate_installs(db, project, log)
        job.status = "success"
        log("[done] بیلد موفق")
    except manifest.ManifestError as e:
        job.status = "failed"
        log(f"[failed] {e}")
    except Exception as e:
        job.status = "failed"
        log(f"[failed] {e}")
        # صداقت (v0.3.29): اگر ریشه، گواهیِ در-انتظارِ دامنهٔ رجیستری است،
        # همان‌جا بگو — نه خطای مبهم داکر. ورکر خودترمیم خودش دوباره تلاش می‌کند.
        _m = str(e)
        if "push ناموفق" in _m and ("tls: failed to verify" in _m or "x509" in _m):
            try:
                if traefik_ops.site_cert_ok(domains.git()) is False:
                    log("گواهی HTTPS دامنهٔ رجیستری (گیت) هنوز صادر نشده — پنجرهٔ انتظار پس از نصب تازه."
                        " این بیلد پس از صادرشدن گواهی خودکار دوباره تلاش می‌شود؛ نیازی به کاری نیست.")
            except Exception:
                pass
    finally:
        job.finished_at = db_now()
        db.commit()
        dockerops.cleanup_build_dir(build_dir)


def _sync_services(db, project: Project, specs, images: dict[str, str],
                   src_kind: str) -> None:
    """ردیف‌های Service پروژه را با سرویس‌های اعلام‌شده هم‌راستا می‌کند.

    tier در مسیر خودکار حفظ می‌شود (انتخاب کاربر هنگام انتشار)؛ در مسیر
    مانیفست، tier اعلام‌شدهٔ مانیفست حرف می‌زند.
    """
    existing = {s.name: s for s in project.services}
    for spec in specs:
        row = existing.pop(spec.name, None)
        if row is None:
            row = Service(project_id=project.id, name=spec.name, status="stopped")
            db.add(row)
        row.image = images.get(spec.name) or spec.image
        row.port = spec.port
        row.command = spec.command
        row.env_json = manifest.json_dumps_env(spec)
        row.volumes_json = manifest.json_dumps_volumes(spec)
        row.is_public = spec.is_public
        if src_kind == "manifest" or row.tier not in settings.TIERS:
            row.tier = spec.tier
        row.container_name = dockerops.service_container(project.id, spec.name)
    for gone in existing.values():
        db.delete(gone)
    db.commit()


def _propagate_installs(db, project: Project, log) -> None:
    """انتشار خودکار اپ به‌روز به نصب‌های مارکت‌پلیس (سرویس‌محور).

    هر نصب، پروژهٔ مالکِ خریدار است؛ ایمیج‌های سرویس‌هایش با تگ تازهٔ فروشنده
    هم‌راستا و اگر روشن بوده، دوباره مستقر می‌شود (روی شبکهٔ خصوصی خودش).
    """
    installs = (db.query(Project)
                .filter(Project.source_project_id == project.id)
                .all())
    for inst in installs:
        changed = False
        for src in project.services:
            svc = next((x for x in inst.services if x.name == src.name), None)
            if svc and (svc.image != src.image or svc.tier != src.tier):
                svc.image = src.image
                svc.tier = src.tier
                changed = True
        if not changed:
            continue
        was_running = bool(inst.sites) and any(s.status == "running" for s in inst.sites)
        try:
            if was_running:
                sites_svc._deploy(db, inst)
                log(f"[market] نصب {inst.slug} با اپ تازه به‌روزرسانی شد")
            else:
                db.commit()
                log(f"[market] نصب {inst.slug} ایمیج تازه گرفت (خاموش است — با روشن‌کردن اعمال می‌شود)")
        except Exception as e:
            db.rollback()
            log(f"[market] به‌روزرسانی نصب {inst.slug} ناموفق: {e}")


# ---------------------------------------------------------------- heal

def _heal_log(msg: str) -> None:
    """شفافیت خودترمیمی (قرار مالک 2026-11-09 «یک روال شفاف»): هر اقدام heal
    هم در stdout (docker logs — دیدنی با parastoo logs) و هم در رینگ‌بافر
    devlog (/api/dev/logs و بخش خطاهای /api/dev/report) ثبت می‌شود.
    ترمیمِ بی‌صدا ممنوع — ناظرِ اصلی (access-agent/مالک) باید ببیند."""
    print(f"[parastoo] {msg}", flush=True)
    logging.getLogger("parastoo").info("[heal] %s", msg)


def heal_once() -> None:
    """خودترمیم‌های شفاف — هر ۱۲۰ ثانیه (هر اقدام با _heal_log ثبت می‌شود).

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
    # فاز ۰) همگام‌سازی پیکربندی ترافیک با قالبِ همین نسخه (v0.3.33) — پیش از
    # هر فاز دیگر. تغییر قالب کد (مثل رزولور wild تازه) تا دیروز فقط با نصب
    # مجدد روی سرور می‌نشست؛ حالا هر استقرار خودش فایل‌ها را با حقیقت دامنه
    # بازنویسی می‌کند. تغییر استاتیک = ری‌استارت ترافیک = چرخهٔ ACME هم از نو.
    try:
        db0 = SessionLocal()
        try:
            _r = traefik_ops.ensure_traefik_config(db0)
        finally:
            db0.close()
        if _r.get("static") or _r.get("dynamic"):
            _heal_log(f"traefik config resynced: {_r}")
    except Exception as e:
        _heal_log(f"traefik config resync failed (continuing): {e}")
    try:
        stale, cur = dockerops.gitea_stale()
    except Exception as e:
        # v0.3.29 درس میدانی: استثنای گذرای فاز گیتا (مثلاً شلوغی Docker API
        # درست بعد از compose up) نباید فاز گواهی را هم قربانی کند — قبلاً
        # return زودهنگام یعنی سه چرخهٔ heal پشت‌سرهم گواهی‌های غایب پنل را
        # هرگز ندیدند. تشخیص ناموفق = «کهنه نیست» و هر دو فاز ادامه می‌یابند.
        print(f"[parastoo] gitea staleness check failed (continuing): {e}")
        stale = False
    if stale:
        _heal_log(f"gitea ROOT_URL stale ({cur or 'unset'}) — recreating "
                  f"with active domain {dockerops.gitea_root_url()}")
        try:
            dockerops.recreate_gitea()
            env_ok = dockerops.sync_host_env()
            _heal_log(f"gitea recreated with active domain "
                      f"(host .env synced={env_ok})")
        except Exception as e:
            _heal_log(f"gitea self-heal failed: {e}")
    _tls_certs_heal(db)
    _build_retry_heal(db)


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

        def _consider(host: str) -> None:
            """گارد DNS + آزمون گواهی برای یک هاست — بدون استثنا."""
            h = (host or "").strip().lower().rstrip(".")
            if not h or h in broken:
                return
            if traefik_ops.resolve_a(h) != ip:
                return  # DNS هنوز به این سرور اشاره ندارد — دست نزنیم
            if traefik_ops.site_cert_ok(h) is False:
                broken.append(h)

        for site in db.query(Site).filter(Site.status == "running").all():
            _consider(site.subdomain)
        # v0.3.29 — زیرساخت هم مثل سایت: پنل/گیت/دامنهٔ پایه/www پس از یک
        # شکست ACME (میدان 2026-11-06: سقف نرخ ۴۲۹ لایهٔ لت‌انکریپت در نصب
        # تازهٔ چهارم) هرگز دوباره تلاش نمی‌کردند — پنل ساعت‌ها/روزها روی
        # گواهی پیش‌فرض می‌ماند و هیچ چیزی آن را ترمیم نمی‌کرد.
        for host in (settings.PANEL_DOMAIN, settings.GIT_DOMAIN,
                     settings.BASE_DOMAIN,
                     f"www.{(settings.BASE_DOMAIN or '').strip()}"):
            _consider(host)
        if not broken or time.time() - _last_tls_restart < TLS_RESTART_GAP:
            return
        _last_tls_restart = time.time()
        _heal_log(f"cert missing for {', '.join(broken)} — "
                  f"restarting traefik to retry ACME")
        c = dockerops.client()
        c.containers.get(settings.TRAEFIK_CONTAINER).restart(timeout=10)
    except Exception as e:
        _heal_log(f"cert self-heal failed: {e}")


def _registry_tls_failure(job_log: str | None) -> bool:
    """آیا لاگ این بیلد، مردودشدن در push به‌خاطر TLS رجیستری است؟"""
    t = job_log or ""
    return "push ناموفق" in t and ("tls: failed to verify" in t or "x509" in t)


def _build_retry_heal(db) -> None:
    """فاز ج) خودترمیم بیلدِ مردودِ رجیستری در پنجرهٔ انتظار گواهی (v0.3.29).

    میدان: تا وقتی گواهی git.<domain> صادر نشده (۴۲۹ سقف نرخ لت‌انکریپت یا
    DNS کند)، دیمون داکر هنگام گرفتن توکن رجیستری TLS را رد می‌کند و بیلد
    می‌میرد — در نصب سالمِ قبل-از-گواهی، هر بیلدی تکرارشدنیِ نامعلوم به نظر
    می‌رسید. وقتی گواهی آماده شد، آخرین بیلد مردود هر پروژه حداکثر یک‌بار
    خودکار دوباره صف می‌شود — کاربر هیچ کاری ندارد. best-effort."""
    global _last_registry_retry
    try:
        if time.time() - _last_registry_retry < REGISTRY_RETRY_GAP:
            return
        if not dockerops.available():
            return
        if traefik_ops.site_cert_ok(domains.git()) is not True:
            return  # گواهی هنوز آماده نیست — دست نزنیم
        projects = db.query(Project).all()
        for project in projects:
            latest = (db.query(Build)
                      .filter(Build.project_id == project.id)
                      .order_by(Build.id.desc()).first())
            if latest is None or latest.status != "failed":
                continue
            if not _registry_tls_failure(latest.log) or "[auto-retry]" in (latest.log or ""):
                continue  # این بیلد یک‌بار فرصت صف مجدد داشته
            busy = (db.query(Build)
                    .filter(Build.project_id == project.id,
                            Build.status.in_(("queued", "running"))).first())
            if busy:
                continue
            _last_registry_retry = time.time()
            latest.log = ((latest.log or "")[-LOG_TAIL:]
                          + "[auto-retry] گواهی رجیستری آماده شد — بیلد خودکار دوباره صف شد\n")
            db.add(Build(project_id=project.id, commit_sha=latest.commit_sha,
                         status="queued"))
            db.commit()
            _heal_log(f"build #{latest.id} died on registry TLS — "
                      f"auto-requeued (cert is ready now)")
            return  # هر چرخه حداکثر یک صف — چرخهٔ بعدی بقیه را می‌گیرد
    except Exception as e:
        _heal_log(f"build retry heal failed: {e}")


# ---------------------------------------------------------------- mobile release

def mobile_once() -> None:
    """یک دور همگام‌سازی رلیز پرستو موبایل (v0.5.9) — فقط وقتی چیزی نصب شد لاگ می‌دهد."""
    summary = mobile.sync_once()
    if summary:
        print(f"[parastoo] mobile release: {summary}")


# ---------------------------------------------------------------- billing

def billing_once() -> None:
    """یک دور صورتحساب با نشست تازه — منطق کسر/توقف در services/billing.py."""
    db = SessionLocal()
    try:
        billing.charge_running_services(db)
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
        # v0.5.14 — خودترمیم زون پیش از گارد DNS (میدان 2026-11-09): زون پایه
        # فقط داخل apply_domain ساخته می‌شد و apply هم فقط پس از نشستن DNS
        # اجرا می‌شد؛ با زونِ غایب (نصب دوباره با پایگاه تازه) حلقه هرگز باز
        # نمی‌شد و انتظارِ معلق تا سقف ۷۲ ساعت می‌ماند. حالا پیش از چک، زون
        # غایب ساخته/همگام می‌شود — همان بیمهٔ v0.3.28؛ زونِ بی‌دیالگیشن هیچ
        # رزولوری نمی‌پرسد، پس بی‌ضرر است و گارد check_dns سرِ جای خود مانده.
        try:
            traefik_ops.sync_base_zone(db, p["base"])
        except Exception:
            pass  # بیمه است، نه پیش‌نیاز — شکستش مسیر فعال‌سازی را نمی‌بندد
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
