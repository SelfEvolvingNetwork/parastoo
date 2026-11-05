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
    DATA_DIR="/tmp/parastoo-smoke-audit",  # پل عامل: فایل audit اینجا نوشته می‌شود
)
if os.path.exists("/tmp/parastoo-test.db"):
    os.remove("/tmp/parastoo-test.db")
if os.path.exists("/tmp/parastoo-smoke-audit/dev/agent-audit.jsonl"):
    os.remove("/tmp/parastoo-smoke-audit/dev/agent-audit.jsonl")

sys.path.insert(0, str(ROOT / "core"))

from fastapi.testclient import TestClient  # noqa: E402

from app import dockerops, security  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Build, Payment, Project, Site, User, WalletTx  # noqa: E402
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

    # رگرسیون 500 داشبورد (v0.2.22): قالب به b.project بعد از بسته‌شدن نشست دست
    # می‌زند؛ بدون joinedload → DetachedInstanceError برای هر کاربر با بیلد.
    r = c.get("/dashboard")
    check("داشبورد کاربرِ دارای بیلد سالم است (رگرسیون DetachedInstanceError)",
          r.status_code == 200 and "در صف" in r.text)

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

    print("— پرداخت آنلاین کیف پول (درگاه آزمایشی v0.3.4) —")
    # اعتبارسنجی مبلغ — هیچ رکوردی نباید بماند
    for label, amount in (("غیرعددی", "abc"), ("زیر کف", "5000"), ("بالای سقف", "99999999")):
        r = c.post("/wallet/charge", data={"amount": amount, "gateway": "mock", "csrf": csrf}, follow_redirects=False)
        check(f"مبلغ {label} رد شد", "m=amount_invalid" in r.headers.get("location", ""))
    r = c.post("/wallet/charge", data={"amount": "30000", "gateway": "stripe", "csrf": csrf}, follow_redirects=False)
    check("درگاه ناشناخته رد شد", "m=gateway_invalid" in r.headers.get("location", ""))
    r = c.post("/wallet/charge", data={"amount": "30000", "gateway": "mock"}, follow_redirects=False)
    check("شارژ بدون CSRF رد شد", "m=csrf" in r.headers.get("location", ""))
    db = SessionLocal()
    check("هیچ درخواست پرداختِ ردشده‌ای رکورد نشد", db.query(Payment).count() == 0)
    db.close()

    # چرخهٔ موفق کامل: فرم → درگاه → بازگشت → شارژ
    r = c.post("/wallet/charge", data={"amount": "30000", "gateway": "mock", "csrf": csrf}, follow_redirects=False)
    loc = r.headers.get("location", "")
    check("شارژ ۳۰هزار → ریدایرکت به درگاه mock", r.status_code == 303 and loc.startswith("/pay/mock/"))
    token = loc.rsplit("/", 1)[-1]
    r = c.get(loc)
    check("صفحهٔ درگاه با مبلغ درست باز می‌شود", r.status_code == 200 and "30,000" in r.text and "درگاه پرداخت آزمایشی" in r.text)
    r = c.get(f"/payment/callback/mock?token={token}&status=paid")
    check("callback موفق → کیف پول با pay_done", r.status_code == 303 and "m=pay_done" in r.headers.get("location", ""))
    db = SessionLocal()
    pay = db.query(Payment).filter(Payment.token == token).first()
    check("پرداخت paid شد و رسید درگاه دارد", pay is not None and pay.status == "paid" and pay.gateway_ref.startswith("MOCK-"))
    u = db.get(User, uid)
    check("موجودی ۳۰هزار اضافه شد (79,200)", u.balance == 79200, f"balance={u.balance}")
    check("WalletTx واریز در دفتر کل ثبت شد", db.query(WalletTx).filter(WalletTx.user_id == uid, WalletTx.kind == "deposit", WalletTx.note.contains("رسید")).count() == 1)
    db.close()

    # idempotency — پخش دوبارهٔ همان callback نباید دوبار شارژ کند
    r = c.get(f"/payment/callback/mock?token={token}&status=paid")
    check("پخش دوبارهٔ callback → pay_dup", "m=pay_dup" in r.headers.get("location", ""))
    db = SessionLocal()
    u = db.get(User, uid)
    check("موجودی تغییری نکرد (idempotent)", u.balance == 79200, f"balance={u.balance}")
    check("فقط یک واریز آنلاین ثبت است", db.query(WalletTx).filter(WalletTx.user_id == uid, WalletTx.kind == "deposit", WalletTx.note.contains("رسید")).count() == 1)
    db.close()
    r = c.get(f"/pay/mock/{token}")
    check("درگاه برای پرداختِ پردازش‌شده دوباره باز نمی‌شود", r.status_code == 303 and "m=pay_dup" in r.headers.get("location", ""))

    # چرخهٔ لغو: هیچ پولی نباید حرکت کند
    r = c.post("/wallet/charge", data={"amount": "20000", "gateway": "mock", "csrf": csrf}, follow_redirects=False)
    token2 = r.headers.get("location", "").rsplit("/", 1)[-1]
    r = c.post(f"/pay/mock/{token2}/result", data={"result": "cancel", "csrf": csrf}, follow_redirects=False)
    check("انصراف در درگاه → ریدایرکت به callback", "/payment/callback/mock" in r.headers.get("location", "") and "status=cancel" in r.headers.get("location", ""))
    r = c.get(r.headers["location"])
    check("لغو پرداخت ثبت شد", "m=pay_canceled" in r.headers.get("location", ""))
    db = SessionLocal()
    pay2 = db.query(Payment).filter(Payment.token == token2).first()
    u = db.get(User, uid)
    check("پرداخت لغوشده هیچ credit نگرفت", pay2.status == "canceled" and u.balance == 79200)
    db.close()

    # مالکیت و مسیرهای مرزی
    c4 = TestClient(app, follow_redirects=False)
    r = c4.get(f"/payment/callback/mock?token={token}&status=paid")
    check("ناشناس → ریدایرکت به ورود", r.status_code == 303 and "login" in r.headers.get("location", ""))
    r = c4.get(f"/pay/mock/{token}")
    check("صفحهٔ درگاه برای ناشناس → ورود", r.status_code == 303 and "login" in r.headers.get("location", ""))
    r = c2.get(f"/pay/mock/{token}")
    check("درگاه mock برای کاربر دیگر → 404 (مالکیت)", r.status_code == 404)
    r = c.get("/payment/callback/mock?token=no-such-token&status=paid")
    check("توکن ناشناخته → not_found", "m=not_found" in r.headers.get("location", ""))
    r = c.get(f"/payment/callback/zarinpal?token={token}&status=paid")
    check("درگاه ناشناخته در callback → not_found", "m=not_found" in r.headers.get("location", ""))
    r = c.get("/wallet")
    check("فرم شارژ آنلاین در کیف پول هست", "wallet/charge" in r.text and "درگاه آزمایشی" in r.text)
    db = SessionLocal()
    tx_note = db.query(WalletTx).filter(WalletTx.user_id == uid, WalletTx.kind == "deposit", WalletTx.note.contains("رسید")).first().note
    check("یادداشت واریز شمارهٔ رسید دارد", "رسید" in tx_note)
    db.close()

    print("— حساب کاربری و تغییر رمز (v0.2.24) —")
    r = c.get("/account")
    check("صفحه حساب کاربری باز می‌شود", r.status_code == 200 and "تغییر رمز عبور" in r.text)
    c3 = TestClient(app, follow_redirects=False)
    r = c3.get("/account")
    check("ناشناس → ریدایرکت به ورود", r.status_code == 303 and "login" in r.headers["location"])
    r = c.post("/account/password", data={"current_password": "wrong", "new_password": "newpass1",
                                         "new_password2": "newpass1", "csrf": csrf}, follow_redirects=False)
    check("رمز فعلی غلط رد شد", "m=bad_current" in r.headers.get("location", ""))
    r = c.post("/account/password", data={"current_password": "secret1", "new_password": "abc",
                                         "new_password2": "abc", "csrf": csrf}, follow_redirects=False)
    check("رمز کوتاه رد شد", "m=pwd_short" in r.headers.get("location", ""))
    r = c.post("/account/password", data={"current_password": "secret1", "new_password": "newpass1",
                                         "new_password2": "newpass2", "csrf": csrf}, follow_redirects=False)
    check("تکرار ناهمخوان رد شد", "m=pwd_mismatch" in r.headers.get("location", ""))
    r = c.post("/account/password", data={"current_password": "secret1", "new_password": "newpass1",
                                         "new_password2": "newpass1", "csrf": csrf}, follow_redirects=False)
    check("تغییر رمز موفق", "m=password_changed" in r.headers.get("location", ""))
    r = c.post("/login", data={"email": "ali@test.ir", "password": "secret1"}, follow_redirects=False)
    check("ورود با رمز کهنه رد شد", "m=bad_login" in r.headers.get("location", ""))
    r = c.post("/login", data={"email": "ali@test.ir", "password": "newpass1"}, follow_redirects=False)
    check("ورود با رمز جدید موفق", r.status_code == 303)

    print("— حذف پروژه (v0.2.24) —")
    # پروژهٔ ادمین برای تست IDOR و مسدودسازی با سایتِ خریدار
    r = c2.post("/projects/new", data={"name": "ادمین‌شاپ", "slug": "admin-shop", "stack": "static",
                                       "csrf": acsrf}, follow_redirects=False)
    check("ساخت پروژهٔ ادمین", r.status_code == 303 and "admin-shop" in r.headers["location"])
    # IDOR: علی حق حذف پروژهٔ ادمین را ندارد
    r = c.post("/projects/admin-shop/delete", data={"confirm": "admin-shop", "csrf": csrf}, follow_redirects=False)
    check("حذف پروژهٔ دیگری → not_found", "m=not_found" in r.headers.get("location", ""))
    # تأیید نامطابق
    r = c.post("/projects/my-shop/delete", data={"confirm": "wrong-slug", "csrf": csrf}, follow_redirects=False)
    check("تأیید نامطابق رد شد", "m=confirm_mismatch" in r.headers.get("location", ""))
    # بدون CSRF
    r = c.post("/projects/my-shop/delete", data={"confirm": "my-shop"}, follow_redirects=False)
    check("حذف بدون CSRF → گارد", "m=csrf" in r.headers.get("location", ""))
    # سایتِ خریدارِ خارجی → مسدود (سایت زندهٔ دیگران نباید بشکند)
    db = SessionLocal()
    ap = db.query(Project).filter(Project.slug == "admin-shop").first()
    buyer_site = Site(project_id=ap.id, owner_id=uid, slug="buyer-site", subdomain="buyer.apps.test",
                      container_name="parastoo-site-buyer-site", internal_port=80, tier="nano",
                      status="running", image="img:latest")
    db.add(buyer_site)
    db.commit()
    db.close()
    r = c2.post("/projects/admin-shop/delete", data={"confirm": "admin-shop", "csrf": acsrf}, follow_redirects=False)
    check("حذف با سایت خریدار خارجی مسدود شد", "m=project_in_use" in r.headers.get("location", ""))
    # حذف موفق پروژهٔ علی (سایت demo-site هم مال خودش است → همراه پروژه حذف می‌شود)
    r = c.post("/projects/my-shop/delete", data={"confirm": "my-shop", "csrf": csrf}, follow_redirects=False)
    check("حذف موفق پروژه", "m=project_deleted" in r.headers.get("location", ""))
    db = SessionLocal()
    check("پروژه از دیتابیس پاک شد", db.query(Project).filter(Project.slug == "my-shop").first() is None)
    check("بیلدهای پروژه پاک شدند", db.query(Build).filter(Build.project_id == pid).count() == 0)
    check("سایت وابستهٔ مالک پاک شد", db.query(Site).filter(Site.slug == "demo-site").first() is None)
    db.close()
    r = c.get("/projects")
    check("لیست پروژه‌ها دیگر my-shop ندارد", "my-shop" not in r.text)

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
# از v0.3.0 منطق مارکت در services/market.py است (مسیر routes نازک شده)
mk_src = ((ROOT / "core/app/services/market.py").read_text()
          + (ROOT / "core/app/routes/marketplace.py").read_text())
check("پورت مارکت‌پلیس از استک/سایت فروشنده (نه هاردکد 3000)",
      "default_port(project.stack)" in mk_src and "internal_port=3000" not in mk_src)

print()
print("— مقاوم‌سازی نصب‌کننده —")
inst = (ROOT / "install.sh").read_text()
check("تلهٔ وقفهٔ نصب (INT/TERM/HUP) تعریف شده",
      "trap on_interrupt INT TERM HUP" in inst)
check("راهنمای تکمیل دستی بعد از وقفه (compose up)",
      "interrupted before it finished" in inst and "docker compose up -d --build" in inst)
check("پیشرفت زندهٔ پروب PyPI/npm (یک خط برای هر میزبان، بدون قدم بی‌صدا)",
      "probing %-44s" in inst and 'probe_full "pypi.org (package index)"' in inst
      and "BLOCKED/timeout" in inst and "silent and can take up to a minute" not in inst)
check("آزمون مسیر واقعی کاربر: پنل از پشت ترافیک در پایان نصب",
      'mark "6b) panel via traefik"' in inst and '-H "Host: $PANEL_DOMAIN"' in inst
      and "docker logs parastoo-traefik" in inst)
bs = (ROOT / "bootstrap.sh").read_text()
_ver = ""
for _line in (ROOT / "core/app/config.py").read_text().splitlines():
    if _line.strip().startswith("VERSION:"):
        _ver = _line.split('"')[1]
        break
check("نام zip پیش‌فرض بوت‌استرپ هم‌نسخهٔ جاری است",
      _ver != "" and f"parastoo-v{_ver}.zip" in bs)

print()
print("— گزارش عیب‌یابی doctor —")
doc = (ROOT / "scripts/doctor.sh").read_text()
check("doctor.sh همهٔ رمزها را مخفی می‌کند (PASSWORD|SECRET|TOKEN)",
      "PASSWORD|SECRET|TOKEN" in doc)
check("doctor.sh وضعیت کانتینرها و سلامت هسته را جمع می‌کند",
      "compose ps" in doc and "healthz" in doc)
check("doctor.sh دسترس‌پذیری Docker Hub/PyPI/npm/GitHub را می‌آزماید",
      doc.count("probe \"") >= 5)
check("doctor.sh گزارش را در backups ذخیره می‌کند",
      "doctor-report-" in doc)
check("نصب‌کننده فازها را ثبت می‌کند (.install-state)",
      ".install-state" in inst and 'mark "1) prerequisites"' in inst and 'mark "done"' in inst)
check("مسیر doctor در die() و تلهٔ وقفه و CLI هست",
      inst.count("scripts/doctor.sh") >= 2 and "doctor)" in (ROOT / "bin/parastoo").read_text())

print()
print("— سازگاری Traefik با Docker Engine 29 —")
compose_src = (ROOT / "docker-compose.yml").read_text()
check("ایمیج ترافیک دقیقاً v3.6.6 پین شده (Docker 29 حداقل API 1.44)",
      "image: traefik:v3.6.6" in compose_src and "traefik:v3.3" not in compose_src)
check("لیست ایمیج‌های آفلاین هم v3.6.6 است",
      "traefik:v3.6.6" in (ROOT / "deploy/images.txt").read_text())
check("doctor خطای «is too old» را با دستور اصلاح گزارش می‌کند",
      "is too old" in doc and "traefik:v3.6.6" in doc)
check("فاز 6b علت ریشهٔ همین خطا را خودش نام می‌برد",
      "grep -q 'is too old'" in inst and "Root cause found" in inst)

print()
print("— ریست کارخانه —")
cli = (ROOT / "bin/parastoo").read_text()
reset_sh = (ROOT / "scripts/factory-reset.sh").read_text()
uni = (ROOT / "uninstall.sh").read_text()
rme = (ROOT / "README.md").read_text()
check("CLI دستور reset به اسکریپت مقاوم factory-reset واگذار می‌شود (v0.2.17+)",
      "reset)" in cli and "factory-reset.sh" in cli)
check("reset تایید صریح دارد (Type 'reset')",
      "Type 'reset'" in reset_sh)
check("reset کانتینرهای سایت (خارج از استک) را قبل از down حذف می‌کند",
      "filter name=parastoo-site-" in reset_sh
      and reset_sh.index("parastoo-site-") < reset_sh.index("down -v"))
check("reset ولوم‌ها و ایمیج‌های پروژه‌ها را هم پاک می‌کند",
      "down -v --remove-orphans" in reset_sh and "label=parastoo.slug" in reset_sh)
check("reset توکن نصب را از .env برمی‌گرداند",
      "PARASTOO_SETUP_TOKEN" in reset_sh)
check("uninstall.sh (حالت ولوم‌ها) هم سایت‌ها و ایمیج‌ها را پاک می‌کند",
      "filter name=parastoo-site-" in uni and "label=parastoo.slug" in uni)
check("README روش ریست کارخانه را مستند کرده",
      "parastoo reset" in rme and "تنظیمات کارخانه" in rme)

print()
print("— بازیابی توکن در ویزارد —")
stok = (ROOT / "core/app/web/templates/setup_token.html").read_text()
check("صفحهٔ توکن، دستور کامل بازیابی را نشان می‌دهد (نه راهنمای مبهم)",
      "grep PARASTOO_SETUP_TOKEN" in stok and "/etc/parastoo-install-path" in stok
      and "sudo grep SETUP .env" not in stok)
check("README فقط یک روش نصب دارد (گیت‌هاب) — بدون setup.sh دستی",
      "sudo bash setup.sh" not in rme and "bootstrap.sh | sudo bash" in rme)

print()
print("— API نسخه و حالت توسعه‌دهنده (v0.2.12) —")
r = c.get("/api/version")
check("GET /api/version عمومی و سبک (بدون لاگین)", r.status_code == 200 and r.json().get("ok") is True
      and r.json().get("version") == _ver)
r = c.get("/healthz")
check("/healthz نسخه و فیلدهای استقرار را دارد", r.status_code == 200 and r.json().get("version") == _ver)
r = c.get("/api/dev/report")
check("حالت توسعه خاموش → /api/dev/report 404 (نه 403)", r.status_code == 404)
r = c.get("/dev")
check("حالت توسعه خاموش → /dev 404", r.status_code == 404)

from app.config import settings as _settings  # noqa: E402
_old_dev, _old_tok = _settings.DEV_MODE, _settings.DEV_TOKEN
_settings.DEV_MODE = True
r = c.get("/api/dev/report")
check("حالت توسعه روشن + کاربر عادی → 404", r.status_code == 404)
r = c2.get("/api/dev/report")
check("حالت توسعه روشن + ادمین → گزارش JSON", r.status_code == 200
      and r.json().get("app", {}).get("version") == _ver)
check("گزارش پرچم‌ها را دارد ولی هیچ رمزی ندارد", "flags" in r.json() and "test-secret-key" not in r.text)
r = c2.get("/api/dev/logs?tail=5")
check("GET /api/dev/logs متن لاگ را می‌دهد", r.status_code == 200)
r = c2.get("/api/dev/updates")
check("GET /api/dev/updates تاریخچه را می‌دهد", r.status_code == 200 and r.json().get("ok") is True)
r = c2.get("/dev")
check("صفحه /dev برای ادمین رندر می‌شود", r.status_code == 200 and "توسعه" in r.text)
# دسترسی با توکن (بدون لاگین)
_settings.DEV_TOKEN = "devtok123"
c3 = TestClient(app, follow_redirects=False)
r = c3.get("/api/dev/report")
check("حالت توسعه + بدون توکن/نشست → 404", r.status_code == 404)
r = c3.get("/api/dev/report", headers={"X-Dev-Token": "devtok123"})
check("دسترسی با هدر X-Dev-Token", r.status_code == 200)
r = c3.get("/api/dev/report?token=devtok123")
check("دسترسی با ?token=", r.status_code == 200)
r = c3.get("/api/dev/report", headers={"X-Dev-Token": "wrong"})
check("توکن غلط → 404", r.status_code == 404)
_settings.DEV_MODE, _settings.DEV_TOKEN = _old_dev, _old_tok

print()
print("— آپدیت خودکار از گیت‌هاب (v0.2.12) —")
au = (ROOT / "scripts/auto-update.sh").read_text()
check("auto-update.sh با flock از اجرای موازی جلوگیری می‌کند", "flock" in au and "parastoo-autoupdate.lock" in au)
check("auto-update.sh کامیت را با git ls-remote مقایسه می‌کند", "git ls-remote" in au and ".parastoo-deploy" in au)
check("جایگزینی فقط پوشه‌های کد است (نه .env و داده‌ها)",
      "rm -rf core bin deploy scripts postgres-init docs" in au)
check("پیش از جایگزینی، بکاپ کد گرفته می‌شود", "backups/code-" in au)
check("تاریخچهٔ استقرار در logs/updates.jsonl ثبت می‌شود", "updates.jsonl" in au)
check("پشتیبانی پراکسی گیت‌هاب (تحریم‌ها)", "PARASTOO_GH_PROXY" in au and "PARASTOO_UPDATE_CHANNEL" in au)
check("حداقل فاصلهٔ بررسی ۶۰ ثانیه اعمال می‌شود", '-lt 60 ] && INTERVAL=60' in au.replace("  ", " "))
check("auto-update.sh کلید خاموش‌کردن (PARASTOO_AUTO_UPDATE) را می‌خواند", "PARASTOO_AUTO_UPDATE" in au)
check("پس از استقرار، سلامت هسته چک می‌شود", "healthz" in au)
inst = (ROOT / "install.sh").read_text()
check("install.sh تایمر systemd را رندر و فعال می‌کند",
      "deploy/systemd/$_u" in inst
      and "systemctl enable --now parastoo-autoupdate.timer" in inst
      and "__INSTALL_DIR__" in inst)
check("install.sh وضعیت اولیهٔ استقرار (.parastoo-deploy) را می‌نویسد", ".parastoo-deploy" in inst)
check("install.sh متغیرهای آپدیت خودکار و حالت توسعه را در .env می‌گذارد",
      "PARASTOO_AUTO_UPDATE=" in inst and "PARASTOO_DEV_MODE=" in inst)
sd_svc = (ROOT / "deploy/systemd/parastoo-autoupdate.service").read_text()
sd_tmr = (ROOT / "deploy/systemd/parastoo-autoupdate.timer").read_text()
check("واحد systemd مسیر نصب را پارامتری دارد", "__INSTALL_DIR__" in sd_svc and "auto-update.sh" in sd_svc)
check("تایمر WantedBy=timers.target دارد (فعال‌پذیر)", "WantedBy=timers.target" in sd_tmr)
check("compose حالت توسعه و کامیت مستقر را به هسته می‌دهد",
      "PARASTOO_DEV_MODE" in compose_src and "PARASTOO_DEPLOYED_COMMIT" in compose_src)
check("تاریخچهٔ آپدیت به هسته سوار می‌شود (ro)", "./logs:/app/host-logs:ro" in compose_src)
cli = (ROOT / "bin/parastoo").read_text()
check("CLI دستور autoupdate (status/on/off/run) دارد", "autoupdate)" in cli and "run)" in cli)
check("CLI update از مسیر آپدیت خودکار (گیت‌هاب) می‌رود", "auto-update.sh" in cli)
uni = (ROOT / "uninstall.sh").read_text()
check("uninstall تایمر آپدیت خودکار را هم پاک می‌کند", "parastoo-autoupdate.timer" in uni)
check("config.py DEV_MODE/DEV_TOKEN/DEPLOYED_COMMIT دارد",
      "PARASTOO_DEV_MODE" in (ROOT / "core/app/config.py").read_text()
      and "PARASTOO_DEPLOYED_COMMIT" in (ROOT / "core/app/config.py").read_text())
check("main.py خطاهای ۵۰۰ را لاگ می‌کند (قابل دریافت در گزارش)",
      "log.exception" in (ROOT / "core/app/main.py").read_text())

gitea_src = (ROOT / "core/app/gitea.py").read_text()
check("create_repo از اندپوینت ادمین گیتا استفاده می‌کند (POST /repos/{owner}/{repo} وجود ندارد و 405 است)",
      "/api/v1/admin/users/{owner}/repos" in gitea_src)
check("اسکوپ پکیج گیتا مفرد است (write:package؛ گیتا 1.23 «write:packages» را 400 رد می‌کند)",
      '"write:package"' in gitea_src
      and '"write:packages"' not in gitea_src.replace('replace("write:packages", "write:package")', ""))
check("create_token روی خطای اسکوپ fallback دارد (نرمال‌سازی جمع→مفرد)",
      "invalid" not in "" and "scope" in gitea_src and "replace(\"write:packages\", \"write:package\")" in gitea_src)
check("کلاینت گیتا کامیت اولیهٔ گروهی (seed) دارد",
      "/contents" in gitea_src and "seed_project_scaffold" in gitea_src and "repo_info" in gitea_src)

print("— اسکفولد استاندارد و پیش‌بازرسی بیلد (v0.3.2) —")
from app import scaffolds, stacks as stacks_mod  # noqa: E402
from app.services import projects as projects_svc  # noqa: E402

for st_key, must in (("static", ["README.md", "index.html", "css/style.css", "js/script.js"]),
                     ("node", ["README.md", "package.json", "server.js"]),
                     ("python", ["README.md", "app.py", "requirements.txt"]),
                     ("docker", ["README.md", "Dockerfile", "index.html"])):
    sc_files = scaffolds.SCAFFOLDS.get(st_key, {})
    check(f"اسکفولد {st_key} کامل است", all(m in sc_files for m in must), f"files={sorted(sc_files)}")
check("اسکفولد node بدون وابستگی است (بیلد اول همیشه سبز)", "dependencies" not in scaffolds.SCAFFOLDS["node"]["package.json"])
check("اسکفولد node قرارداد PORT را دارد", "process.env.PORT" in scaffolds.SCAFFOLDS["node"]["server.js"])
check("اسکفولد پایتون FastAPI است", "FastAPI" in scaffolds.SCAFFOLDS["python"]["app.py"])
check("README اسکفولد پایتون جای دستور اجرا را دارد", "__START_CMD__" in scaffolds.SCAFFOLDS["python"]["README.md"])
check("داکرفایل نمونهٔ مدل docker پورت 3000 را رعایت می‌کند", "EXPOSE 3000" in scaffolds.SCAFFOLDS["docker"]["Dockerfile"])

# پیش‌فرض دستور اجرای پایتون = ورکر uvicorn (ASGI) — با پروژهٔ یک‌بارمصرف می‌سازیم و پاک می‌کنیم
db = SessionLocal()
_u = db.query(User).filter(User.email == "ali@test.ir").first()
db.close()
db = SessionLocal()
_p, _flash = projects_svc.create_project(db, _u, "ای‌پی‌آی تست", "py-api-check", "python")
check("پروژهٔ پایتون بدون دستور اجرا ساخته شد (pending بدون گیتا)", _p is not None and _flash == "git_pending")
check("دستور اجرای پیش‌فرض پایتون = gunicorn با ورکر uvicorn",
      _p is not None and _p.start_cmd == scaffolds.DEFAULT_START_CMD["python"],
      f"start_cmd={getattr(_p, 'start_cmd', None)!r}")
if _p is not None:
    projects_svc.delete_project(db, _p, _u)  # پاکسازی — بدون اثر باقی‌مانده
db = SessionLocal()
check("پروژهٔ یک‌بارمصرف پایتون پاکسازی شد",
      db.query(Project).filter(Project.slug == "py-api-check").count() == 0)
db.close()

import tempfile  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    _d = _Path(_td)
    check("پیش‌بازرسی: node بدون package.json پیام گویا می‌دهد", bool(stacks_mod.missing_marker("node", _d)))
    check("پیش‌بازرسی: python بدون requirements.txt پیام گویا می‌دهد", bool(stacks_mod.missing_marker("python", _d)))
    check("پیش‌بازرسی: static و docker همیشه سبزند",
          stacks_mod.missing_marker("static", _d) is None and stacks_mod.missing_marker("docker", _d) is None)
    (_d / "package.json").write_text("{}\n")
    check("پیش‌بازرسی: node با package.json سبز است", stacks_mod.missing_marker("node", _d) is None)

workers_src = (ROOT / "core/app/workers.py").read_text()
check("مسیر pending (ورکر) هم اسکفولد می‌کارد", "seed_scaffold" in workers_src)
check("بیلد قبل از ساخت قالب، پیش‌بازرسی استک می‌کند", "missing_marker" in workers_src)

# ---------------- پل دسترسی عامل (v0.3.7): exec/netcheck/sql — فقط حالت توسعه ----------------
from app.config import settings as _settings  # noqa: E402
from app.services import agentops as _agentops  # noqa: E402

print("— پل دسترسی عامل: گارد در حالت تجاری (dev خاموش) —")
r = c.post("/api/dev/exec", json={"cmd": "echo hi"})
check("exec با dev خاموش → 404", r.status_code == 404)
r = c.get("/api/dev/netcheck")
check("netcheck با dev خاموش → 404", r.status_code == 404)
r = c.post("/api/dev/sql", json={"sql": "SELECT 1"})
check("sql با dev خاموش → 404", r.status_code == 404)
r = c.get("/api/dns/zones")
check("dns API با dev خاموش + کاربر عادی → 404", r.status_code == 404)

_old_dev, _old_token = _settings.DEV_MODE, _settings.DEV_TOKEN
_settings.DEV_MODE, _settings.DEV_TOKEN = True, "smoke-dev-token"
DEVH = {"X-Dev-Token": "smoke-dev-token"}
try:
    print("— پل دسترسی عامل: گارد توکن —")
    r = c.post("/api/dev/exec", json={"cmd": "echo hi"})
    check("exec بدون توکن → 404", r.status_code == 404)
    r = c.post("/api/dev/exec", json={"cmd": "echo hi"}, headers={"X-Dev-Token": "wrong"})
    check("exec با توکن غلط → 404", r.status_code == 404)
    r = c.post("/api/dev/exec", json={"cmd": ""}, headers=DEVH)
    check("exec با دستور خالی → 400", r.status_code == 400)
    r = c.post("/api/dev/exec", json={"cmd": "echo hi", "where": "kernel"}, headers=DEVH)
    check("exec با where نامعتبر → 400", r.status_code == 400)
    r = c.get("/api/dev/netcheck?url=ftp://x.example", headers=DEVH)
    check("netcheck با scheme نامعتبر → 400", r.status_code == 400)
    r = c.get("/api/dev/netcheck?groups=nogroup", headers=DEVH)
    check("netcheck با گروه ناشناخته → 400", r.status_code == 400)

    print("— پل دسترسی عامل: exec با داکر ماک‌شده —")
    _fake_host = {"where": "host", "cmd_len": 7, "exit_code": 0, "timed_out": False,
                  "stdout": "hi\n", "stderr": "", "duration_ms": 12}
    _orig_run_host, _orig_run_core = _agentops.run_host, _agentops.run_in_core
    _agentops.run_host = lambda cmd, timeout_s=120: dict(_fake_host, cmd_len=len(cmd))
    _agentops.run_in_core = lambda cmd: dict(_fake_host, where="core")
    try:
        r = c.post("/api/dev/exec", json={"cmd": "echo hi"}, headers=DEVH)
        check("exec روی هاست → 200 + خروجی", r.status_code == 200 and r.json()["stdout"] == "hi\n"
              and r.json()["exit_code"] == 0)
        r = c.post("/api/dev/exec", json={"cmd": "echo hi", "where": "core"}, headers=DEVH)
        check("exec داخل هسته → 200", r.status_code == 200 and r.json()["where"] == "core")
    finally:
        _agentops.run_host, _agentops.run_in_core = _orig_run_host, _orig_run_core

    print("— پل دسترسی عامل: مسیر واقعی exec با داکر فِیک (پوشش نرمال‌سازی جریان‌ها) —")
    class _FakeApi:
        def create_host_config(self, **k): return {"HostConfig": True}
        def create_container(self, **k): return {"Id": "fake-id"}
        def start(self, cid): pass
        def wait(self, cid, condition=None, timeout=None): return {"StatusCode": 0}
        def logs(self, cid, stdout=True, stderr=False, **k):
            return b"host-out" if stdout else b"host-err"
        def remove_container(self, cid, force=False, v=False): pass
    class _FakeContainers:
        def get(self, name):
            class _C:  # exec_run با جریان stderr=None — همان الگوی واقعی docker-py
                class res:
                    output = (b"core-out", None)
                    exit_code = 0
                def exec_run(self, cmd, demux=False, workdir=None): return self.res
            return _C()
    class _FakeDockerClient:
        api = _FakeApi()
        containers = _FakeContainers()
    _orig_dcli = _agentops.dockerops.client
    _agentops.dockerops.client = lambda: _FakeDockerClient()
    try:
        r = _agentops.run_host("echo x", 30)
        check("run_host → logs دوفراخوانیِ جدا + خروجی درست",
              r["stdout"] == "host-out" and r["stderr"] == "host-err" and r["exit_code"] == 0)
        r = _agentops.run_in_core("echo x")
        check("run_in_core → جریان None → خروجی حفظ، stderr خالی",
              r["stdout"] == "core-out" and r["stderr"] == "" and r["exit_code"] == 0)
    finally:
        _agentops.dockerops.client = _orig_dcli

    print("— پل دسترسی عامل: netcheck با probe ماک‌شده —")
    _orig_probe = _agentops.probe_url
    _agentops.probe_url = lambda url, timeout_s=8.0: {
        "url": url, "ok": True, "status": 200, "latency_ms": 11, "ip": "1.2.3.4", "error": None}
    try:
        r = c.get("/api/dev/netcheck?groups=pip", headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        check("netcheck گروه pip → 2 هدف زنده", r.status_code == 200
              and d.get("n_total") == 2 and d.get("n_ok") == 2)
        r = c.get("/api/dev/netcheck?url=https://example.com", headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        check("netcheck url دلخواه → هدف تکی", r.status_code == 200
              and d.get("n_total") == 1 and d["targets"][0]["url"] == "https://example.com")
    finally:
        _agentops.probe_url = _orig_probe

    print("— پل دسترسی عامل: SQL فقط‌خواندنی —")
    r = c.post("/api/dev/sql", json={"sql": "SELECT 1 AS one"}, headers=DEVH)
    d = r.json() if r.status_code == 200 else {}
    check("SELECT ساده → 200 + ردیف", r.status_code == 200 and d.get("rows") == [[1]])
    r = c.post("/api/dev/sql", json={"sql": "SELECT count(*) AS n FROM users"}, headers=DEVH)
    check("SELECT شمارش کاربران → 200", r.status_code == 200
          and r.json()["rows"][0][0] >= 1)
    for bad, why in [("DELETE FROM users", "DELETE رد شد"),
                     ("SELECT 1; DROP TABLE users", "چنددستوری رد شد"),
                     ("WITH x AS (SELECT 1) INSERT INTO users(id) SELECT * FROM x", "WITH+INSERT رد شد"),
                     ("UPDATE users SET name='x'", "UPDATE رد شد"),
                     ("DROP TABLE users", "DROP رد شد")]:
        r = c.post("/api/dev/sql", json={"sql": bad}, headers=DEVH)
        check(f"sql: {why}", r.status_code == 400)

    print("— DNS اختصاصی (v0.3.9): گارد + چرخهٔ کامل زون/رکورد با پاوردی‌ان‌اسِ ماک —")
    import json as _json
    import urllib.parse as _up
    import httpx as _httpx
    from app.services import dnsops as _dnsops

    _pzones: dict = {}   # fqdn (با نقطه) → {"kind", "rrsets": [...]}

    def _pdns(request: _httpx.Request) -> _httpx.Response:
        path = _up.unquote(request.url.path)
        p = path[len("/api/v1"):] if path.startswith("/api/v1") else path
        if p == "/servers/localhost" and request.method == "GET":
            return _httpx.Response(200, json={"id": "localhost", "version": "4.9.18-smoke"})
        if p == "/servers/localhost/zones" and request.method == "GET":
            return _httpx.Response(200, json=[{"name": k, "kind": v["kind"], "serial": 1}
                                              for k, v in _pzones.items()])
        if p == "/servers/localhost/zones" and request.method == "POST":
            body = _json.loads(request.content)
            if body["name"] in _pzones:
                return _httpx.Response(409, json={"error": "Conflict: zone already exists"})
            _pzones[body["name"]] = {"kind": body.get("kind", "Master"),
                                     "rrsets": [dict(r) for r in body.get("rrsets", [])]}
            return _httpx.Response(201, json={"name": body["name"]})
        if p.startswith("/servers/localhost/zones/"):
            zid = p[len("/servers/localhost/zones/"):]
            if zid not in _pzones:
                return _httpx.Response(404, json={"error": "Could not find domain"})
            if request.method == "GET":
                return _httpx.Response(200, json={"name": zid, "kind": _pzones[zid]["kind"],
                                                  "serial": 1, "rrsets": _pzones[zid]["rrsets"]})
            if request.method == "PATCH":
                body = _json.loads(request.content)
                store = _pzones[zid]["rrsets"]
                for rr in body.get("rrsets", []):
                    nm, tp = rr["name"], rr["type"]
                    store = [x for x in store if not (x["name"] == nm and x["type"] == tp)]
                    if rr.get("changetype") != "DELETE":
                        store.append({"name": nm, "type": tp, "ttl": rr.get("ttl", 3600),
                                      "records": list(rr.get("records", []))})
                _pzones[zid]["rrsets"] = store
                return _httpx.Response(204)
            if request.method == "DELETE":
                del _pzones[zid]
                return _httpx.Response(204)
        return _httpx.Response(404, json={"error": "no route"})

    _dnsops._FORCE_TRANSPORT = _httpx.MockTransport(_pdns)
    try:
        r = c.get("/api/dns/zones")
        check("dns API کاربر عادی → 404", r.status_code == 404)
        r = c.get("/api/dns/zones", headers=DEVH)
        check("dns API با توکن → 200 و فهرست خالی", r.status_code == 200 and r.json().get("zones") == [])
        r = c.get("/api/dns/status", headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        check("dns status → در دسترس + ns روی دامنهٔ پایه", r.status_code == 200 and d.get("available") is True
              and d.get("nameservers") == ["ns1.127.0.0.1.nip.io", "ns2.127.0.0.1.nip.io"]
              and d.get("server_ip") == "127.0.0.1" and "A" in (d.get("record_types") or []))

        r = c.post("/api/dns/zones", json={"name": "customer-one.example.com"}, headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        zrecs = (d.get("zone", {}) or {}).get("records") or []
        recs = {(x["name"], x["type"]) for x in zrecs}
        check("ساخت زون مشتری → NS/A/ریشه/www/SOA پیش‌فرض", r.status_code == 200
              and ("customer-one.example.com", "NS") in recs
              and ("www.customer-one.example.com", "A") in recs
              and ("customer-one.example.com", "A") in recs
              and any(x["type"] == "SOA" and (x["contents"] or [""])[0].startswith("ns1.") for x in zrecs))
        r = c.post("/api/dns/zones", json={"name": "customer-one.example.com"}, headers=DEVH)
        check("ساخت زون تکراری → 409", r.status_code == 409)
        r = c.post("/api/dns/zones", json={"name": "bad name!!"}, headers=DEVH)
        check("نام زون نامعتبر → 400", r.status_code == 400)

        r = c.post("/api/dns/zones", json={"name": "127.0.0.1.nip.io", "kind": "base"}, headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        recs = {(x["name"], x["type"]) for x in ((d.get("zone", {}) or {}).get("records") or [])}
        check("ساخت زون دامنهٔ اصلی → ns1/ns2/panel/git/apps/وایلدکارد", r.status_code == 200
              and ("ns1.127.0.0.1.nip.io", "A") in recs and ("ns2.127.0.0.1.nip.io", "A") in recs
              and ("panel.127.0.0.1.nip.io", "A") in recs and ("*.apps.127.0.0.1.nip.io", "A") in recs)

        r = c.post("/api/dns/zones/customer-one.example.com/records",
                   json={"name": "test", "type": "A", "content": "1.2.3.4"}, headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        recs = {(x["name"], x["type"]) for x in ((d.get("zone", {}) or {}).get("records") or [])}
        check("افزودن رکورد نسبی → FQDN با نقطهٔ پایانی", r.status_code == 200
              and ("test.customer-one.example.com", "A") in recs)
        r = c.post("/api/dns/zones/customer-one.example.com/records",
                   json={"name": "bad", "type": "A", "content": "999.1.1.1"}, headers=DEVH)
        check("محتوای A نامعتبر → 400", r.status_code == 400)
        r = c.post("/api/dns/zones/customer-one.example.com/records",
                   json={"name": "x", "type": "BOGUS", "content": "v"}, headers=DEVH)
        check("نوع ناشناخته → 400", r.status_code == 400)
        r = c.post("/api/dns/zones/customer-one.example.com/records",
                   json={"name": "_dmarc", "type": "TXT", "content": "v=DMARC1; p=none"}, headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        txt = next((x for x in ((d.get("zone", {}) or {}).get("records") or []) if x["type"] == "TXT"), {})
        check("TXT خودکار کوتیشن می‌گیرد", r.status_code == 200
              and txt.get("contents") == ['"v=DMARC1; p=none"'])
        r = c.delete("/api/dns/zones/customer-one.example.com/records?name=test&type=A", headers=DEVH)
        d = r.json() if r.status_code == 200 else {}
        recs = {(x["name"], x["type"]) for x in ((d.get("zone", {}) or {}).get("records") or [])}
        check("حذف رکورد → از زون می‌رود", r.status_code == 200
              and ("test.customer-one.example.com", "A") not in recs)
        r = c.get("/api/dns/zones", headers=DEVH)
        check("دو زون در فهرست", r.status_code == 200 and len(r.json().get("zones") or []) == 2)

        print("— DNS اختصاصی: UI ادمین —")
        db2 = SessionLocal()
        _adm = db2.query(User).filter(User.email == "admin@test.ir").first()
        _adm_csrf = security.csrf_token(_adm.id)
        db2.close()
        r = c2.get("/admin/dns")
        check("صفحهٔ /admin/dns رندر می‌شود", r.status_code == 200 and "نیم‌سرور" in r.text)
        r = c2.get("/admin/dns/customer-one.example.com")
        check("صفحهٔ زون رندر می‌شود", r.status_code == 200 and "رکوردها" in r.text)
        r = c2.post("/admin/dns/create", data={"name": "ui-zone.example.com", "kind": "customer", "csrf": _adm_csrf},
                    follow_redirects=False)
        check("ساخت زون از UI → ریدایرکت به صفحهٔ زون", r.status_code == 303
              and r.headers.get("location", "").startswith("/admin/dns/ui-zone.example.com"))
        r = c2.post("/admin/dns/ui-zone.example.com/record",
                    data={"name": "", "type": "A", "content": "10.0.0.5", "ttl": "", "csrf": _adm_csrf},
                    follow_redirects=False)
        check("افزودن رکورد از UI", r.status_code == 303 and "m=dns_record_saved" in r.headers.get("location", ""))
        r = c2.post("/admin/dns/ui-zone.example.com/record",
                    data={"name": "bad", "type": "A", "content": "nope", "csrf": _adm_csrf},
                    follow_redirects=False)
        check("رکورد نامعتبر از UI → dns_invalid", r.status_code == 303
              and "m=dns_invalid" in r.headers.get("location", ""))
        r = c2.post("/admin/dns/ui-zone.example.com/record_delete",
                    data={"name": "", "type": "A", "csrf": _adm_csrf}, follow_redirects=False)
        check("حذف رکورد از UI", r.status_code == 303 and "m=dns_record_deleted" in r.headers.get("location", ""))
        r = c2.post("/admin/dns/ui-zone.example.com/delete", data={"csrf": _adm_csrf}, follow_redirects=False)
        check("حذف زون از UI", r.status_code == 303 and "m=dns_zone_deleted" in r.headers.get("location", ""))
    finally:
        _dnsops._FORCE_TRANSPORT = None

    print("— پل دسترسی عامل: audit —")
    _audit = Path("/tmp/parastoo-smoke-audit/dev/agent-audit.jsonl")
    check("فایل audit ساخته شد و رکورد exec دارد", _audit.is_file()
          and any(_json.loads(l)["kind"] == "exec" for l in _audit.read_text().splitlines() if l.strip()))
    check("رکورد audit عملیات‌های DNS هم ثبت شده", any(_json.loads(l)["kind"] == "dns"
          for l in _audit.read_text().splitlines() if l.strip()))
finally:
    _settings.DEV_MODE, _settings.DEV_TOKEN = _old_dev, _old_token

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
