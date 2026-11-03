"""تست دودی پرستو — اجرای همه جریان‌های اصلی بدون داکر/گیتا (حالت pending)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.update(
    DATABASE_URL="sqlite:////tmp/parastoo-test.db",
    PARASTOO_DISABLE_WORKERS="1",
    SECRET_KEY="test-secret-key",
    BASE_DOMAIN="127.0.0.1.nip.io",
    TLS_ENABLED="false",
    REGISTER_OPEN="true",
)
if os.path.exists("/tmp/parastoo-test.db"):
    os.remove("/tmp/parastoo-test.db")

sys.path.insert(0, str(ROOT / "core"))

from fastapi.testclient import TestClient  # noqa: E402

from app import dockerops, security  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Build, Project, Site, User, WalletTx  # noqa: E402
from app.workers import billing_once  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name} {extra}")


with TestClient(app, follow_redirects=False) as c:
    print("— ویزارد راه‌اندازی —")
    r = c.get("/")
    check("GET / بدون ادمین → ریدایرکت به setup", r.status_code == 303 and "/setup" in r.headers["location"])
    r = c.get("/setup")
    check("صفحه setup باز می‌شود", r.status_code == 200 and "راه‌اندازی" in r.text)
    r = c.post("/setup", data={"email": "admin@test.ir", "password": "secret1", "name": "مدیر"}, follow_redirects=False)
    check("POST setup → ریدایرکت", r.status_code == 303)
    check("کوکی نشست ست شد", "parastoo_session" in r.cookies)
    r = c.get("/dashboard")
    check("داشبورد ادمین باز می‌شود (رندر قالب سالم)", r.status_code == 200 and "داشبورد" in r.text)

    print("— ثبت‌نام کاربر عادی —")
    c.get("/logout")
    r = c.post("/register", data={"email": "ali@test.ir", "password": "secret1", "name": "علی"}, follow_redirects=False)
    check("ثبت‌نام → ریدایرکت + کوکی", r.status_code == 303 and "parastoo_session" in r.cookies)
    r = c.get("/dashboard")
    check("داشبورد کاربر جدید", r.status_code == 200)

    print("— ساخت پروژه (گیتا در دسترس نیست → pending) —")
    db = SessionLocal()
    u = db.query(User).filter(User.email == "ali@test.ir").first()
    uid = u.id
    db.close()
    csrf = security.csrf_token(uid)

    r = c.post("/projects/new", data={"name": "فروشگاه من", "slug": "my-shop", "stack": "python",
                                      "start_cmd": "gunicorn -b 0.0.0.0:3000 main:app", "csrf": csrf},
               follow_redirects=False)
    check("ساخت پروژه → ریدایرکت", r.status_code == 303 and "my-shop" in r.headers["location"])
    r = c.get("/projects/my-shop")
    check("صفحه پروژه باز می‌شود", r.status_code == 200)
    check("وضعیت مخزن pending نمایش داده شده", "در حال ساخت" in r.text)
    check("راهنمای گیت نمایش داده نشده (چون آماده نیست)", "git init" not in r.text)

    r = c.post("/projects/new", data={"name": "x", "slug": "panel", "stack": "static", "csrf": csrf}, follow_redirects=False)
    check("slug رزرو رد شد", "slug_reserved" in r.headers.get("location", ""))
    r = c.post("/projects/new", data={"name": "x", "slug": "my-shop", "stack": "static", "csrf": csrf}, follow_redirects=False)
    check("slug تکراری رد شد", "slug_taken" in r.headers.get("location", ""))

    print("— محافظ CSRF —")
    r = c.post("/projects/new", data={"name": "x", "slug": "no-csrf", "stack": "static"}, follow_redirects=False)
    check("بدون CSRF → ریدایرکت m=csrf", "m=csrf" in r.headers.get("location", ""))

    print("— وب‌هوک گیتا —")
    db = SessionLocal()
    p = db.query(Project).filter(Project.slug == "my-shop").first()
    pid = p.id
    secret = p.webhook_secret
    db.close()
    import hashlib, hmac, json
    body = json.dumps({"ref": "refs/heads/main", "after": "abc1234def5678"}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = c.post(f"/api/hooks/gitea/{pid}", content=body,
               headers={"Content-Type": "application/json", "X-Gitea-Signature": sig})
    check("وب‌هوک با امضای درست پذیرفته شد", r.status_code == 200 and r.json().get("ok") is True)
    db = SessionLocal()
    check("بیلد در صف قرار گرفت", db.query(Build).filter(Build.project_id == pid, Build.status == "queued").count() == 1)
    db.close()
    r = c.post(f"/api/hooks/gitea/{pid}", content=body,
               headers={"Content-Type": "application/json", "X-Gitea-Signature": "bad" * 8})
    check("امضای غلط رد شد", r.json().get("ok") is False)
    body_branch = json.dumps({"ref": "refs/heads/dev", "after": "aaa"}).encode()
    sig2 = hmac.new(secret.encode(), body_branch, hashlib.sha256).hexdigest()
    r = c.post(f"/api/hooks/gitea/{pid}", content=body_branch,
               headers={"Content-Type": "application/json", "X-Gitea-Signature": sig2})
    db = SessionLocal()
    check("push به برنچ غیر main بیلد نمی‌گیرد", db.query(Build).count() == 1)
    db.close()

    print("— صفحات دیگر —")
    for path, needle in [("/projects", "پروژه‌ها"), ("/marketplace", "مارکت‌پلیس"),
                         ("/wallet", "کیف پول"), ("/sites", "سایت‌های من")]:
        r = c.get(path)
        check(f"GET {path}", r.status_code == 200 and needle in r.text)

    r = c.get("/admin")
    check("کاربر عادی به ادمین دسترسی ندارد", r.status_code == 303)

    print("— صورت‌حساب ساعتی —")
    # مونکی‌پچ توقف داکر (در تست داکر نداریم)
    dockerops.stop_site = lambda slug: None
    db = SessionLocal()
    s = Site(project_id=pid, owner_id=uid, slug="demo-site", subdomain="demo.apps.test",
             container_name="parastoo-site-demo-site", internal_port=3000, tier="nano",
             status="running", image="127.0.0.1:3000/u/x:latest")
    db.add(s)
    db.commit()
    db.close()
    billing_once()
    billing_once()  # بار دوم نباید دوباره کسر کند (< 1 ساعت)
    db = SessionLocal()
    u = db.get(User, uid)
    check("یک بار هزینه نانو کسر شد (800-)", u.balance == -800, f"balance={u.balance}")
    check("سایت به‌خاطر بدهی متوقف شد", db.query(Site).filter(Site.slug == "demo-site").first().status == "paused")
    check("تراکنش ثبت شد", db.query(WalletTx).filter(WalletTx.user_id == uid, WalletTx.kind == "charge").count() == 1)
    db.close()

    print("— پنل ادمین —")
    db = SessionLocal()
    admin = db.query(User).filter(User.is_admin.is_(True)).first()
    aid = admin.id
    db.close()
    c2 = TestClient(app, follow_redirects=False)
    r = c2.post("/login", data={"email": "admin@test.ir", "password": "secret1"}, follow_redirects=False)
    check("ورود ادمین", r.status_code == 303)
    r = c2.get("/admin")
    check("پنل ادمین باز می‌شود", r.status_code == 200 and "پنل مدیریت" in r.text)
    acsrf = security.csrf_token(aid)
    r = c2.post(f"/admin/users/{uid}/credit", data={"amount": "50000", "note": "تست", "csrf": acsrf}, follow_redirects=False)
    check("شارژ کیف پول توسط ادمین", "m=credited" in r.headers.get("location", ""))
    db = SessionLocal()
    u = db.get(User, uid)
    check("موجودی پس از شارژ 49200", u.balance == 49200, f"balance={u.balance}")
    db.close()
    r = c2.get("/sites")
    check("ادمین صفحه سایت‌ها", r.status_code == 200)

print()
print("— واحدهای کوچک —")
from app import stacks  # noqa: E402

check("slugify", security.slugify("Hello World Test!") == "hello-world-test")
check("hash/verify رمز", security.verify_password("abc123", security.hash_password("abc123")))
check("رمز غلط رد می‌شود", not security.verify_password("bad", security.hash_password("abc123")))
check("داکرفایل node", "npm" in stacks.dockerfile_for("node") and "start" in stacks.dockerfile_for("node"))
check("داکرفایل python با start_cmd", "main:app" in stacks.dockerfile_for("python", "gunicorn -b 0.0.0.0:3000 main:app"))
check("داکرفایل docker → None", stacks.dockerfile_for("docker") is None)
check("پورت پیش‌فرض استاتیک 80", stacks.default_port("static") == 80)

print()
print("— وابستگی‌ها و رگرسیون‌ها (گزارش بازبینی) —")
req = (ROOT / "core/requirements.txt").read_text()
check("fastapi دقیقاً پین شده (نصب تازه تکرارپذیر)", "fastapi==0.115.6" in req)
check("سقف starlette (<0.42) صریح در requirements", "<0.42" in req)
deps_src = (ROOT / "core/app/deps.py").read_text()
check("render با امضای جدید TemplateResponse (request اول)",
      "TemplateResponse(request, name" in deps_src)
mk_src = (ROOT / "core/app/routes/marketplace.py").read_text()
check("پورت مارکت‌پلیس از استک/سایت فروشنده (نه هاردکد 3000)",
      "default_port(project.stack)" in mk_src and "internal_port=3000" not in mk_src)

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
