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
check("گارد زروسsl روی دامنهٔ .ir در نصب‌کننده (درس میدانی دور ششم)",
      "ZeroSSL does not issue certificates for .ir domains" in inst
      and "DNS identifier is disallowed" in inst and "(^|\\.)ir$" in inst)
bs = (ROOT / "bootstrap.sh").read_text()
_ver = ""
for _line in (ROOT / "core/app/config.py").read_text().splitlines():
    if _line.strip().startswith("VERSION:"):
        _ver = _line.split('"')[1]
        break
check("نام zip پیش‌فرض بوت‌استرپ هم‌نسخهٔ جاری است",
      _ver != "" and f"parastoo-v{_ver}.zip" in bs)
check("نصب main تاربال زندهٔ مخزن را بر zip ریلیز (احتمالاً عقب‌مانده) ترجیح می‌دهد (v0.3.28)",
      '[ "$REF" = "main" ]' in bs and "live main" in bs
      and 'may lag main' in bs)
check("zip ریلیز استمپ build خودش (.release-commit) را حمل می‌کند و bootstrap همان را ثبت می‌کند",
      ".release-commit" in bs and "Release stamp honored" in bs
      and "no .release-commit stamp" in bs)
_pub = (ROOT / "scripts/github_publish.sh").read_text()
check("github_publish.sh zip را از همان درختی که push کرده می‌سازد و استمپ می‌گذارد",
      "PUSHED_SHA" in _pub and ".release-commit" in _pub and "zipstage" in _pub)
_to = (ROOT / "core/app/traefik_ops.py").read_text()
check("فعال‌سازی دامنه، زون غایب را خودش provision می‌کند (پایان lame delegation v0.3.28)",
      'provision_zone(z, db=db, kind="base")' in _to
      and "status != 404" in _to and "_delegation_points_here" not in _to)

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
check("doctor.sh نسخه را از مهر استقرار (.parastoo-deploy) می‌خواند نه grep قدیمی README (میدان v0.3.29: روی نصب v0.3.29 گزارش v0.3.16 می‌داد)",
      "s/^version=//p" in doc and ".parastoo-deploy" in doc)
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
print("— تجربهٔ کاربری: پیام‌ها، دوجهته‌بودن، موبایل (v0.3.21) —")
r = c.get("/آدرس-موجود-نیست")  # مسیر ناموجود → صفحهٔ خطای ۴۰۴
check("صفحهٔ ۴۰۴ پیام فارسی روشن دارد (نه Not Found خام)",
      r.status_code == 404 and "پیدا نشد" in r.text and "Not Found" not in r.text)
from app.tpl import FLASHES as _flash_keys  # noqa: E402
check("شکست روشن‌کردن سایت، پیام مخصوص خودش را دارد (نه «انتشار ناموفق»)",
      "site_start_failed" in _flash_keys and "site_stop_failed" in _flash_keys)
check("خریدِ مارکت با شکستِ روشن‌شدن، تکلیف پول و قدم بعدی را می‌گوید",
      "purchase_needs_start" in _flash_keys
      and "هزینه از کیف پول کسر شده" in _flash_keys["purchase_needs_start"])
_svc = (ROOT / "core/app/services/sites.py").read_text()
check("سرویس سایت‌ها شکست start/stop را به کلیدهای جدید نگاشت می‌کند",
      _svc.count('return "site_start_failed"') == 1
      and _svc.count('return "site_stop_failed"') == 1)
_mp = (ROOT / "core/app/routes/marketplace.py").read_text()
check("مسیر مارکت‌پلیس شکست انتشار خرید را به purchase_needs_start می‌فرستد",
      "purchase_needs_start" in _mp)
_css = (ROOT / "core/app/web/static/style.css").read_text()
check("توکن‌های لاتین (mono) isolate هستند — جملهٔ فارسی به‌هم نمی‌ریزد",
      "unicode-bidi: isolate" in _css)
check("جدول w60 ویزارد دامنه عرض واقعی دارد (w50 و w60 هر دو ۶۰٪)",
      ".w50, .w60 { width: 60%; }" in _css)
check("اسکرول‌بار بلوک‌های کد پنهان نیست (دستور بلند «بریده» دیده نمی‌شود)",
      "scrollbar-color" in _css and ".code::-webkit-scrollbar" in _css)
check("کارت‌های آمار داشبورد در موبایل ۲×۲ می‌شوند",
      ".grid-4 { grid-template-columns: repeat(2, 1fr); }" in _css)
_newp = (ROOT / "core/app/web/templates/project_new.html").read_text()
check("فیلد «دستور اجرا» فقط برای استک Python نشان داده می‌شود",
      'id="startcmd-label"' in _newp and "sel.value === 'python'" in _newp)
_au = (ROOT / "scripts/auto-update.sh").read_text()
check("auto-update متادیتای استقرار را صریح صادر می‌کند (وگرنه healthz یک چرخه عقب می‌افتد)",
      "set -a; . ./.env; set +a" in _au
      and 'export PARASTOO_DEPLOYED_COMMIT="$REMOTE"' in _au
      and _au.find("set -a; . ./.env; set +a") < _au.find('export PARASTOO_DEPLOYED_COMMIT="$REMOTE"'))

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

# ---------------- پل دسترسی عامل: netcheck/sql — فقط حالت توسعه ----------------
# (مسیر exec «معادل SSH» در v0.3.11 به دستور مالک کامل حذف شد؛ تنها مسیر اجرای
#  دستور عامل روی سرور، access-agent مستقل است.)
from app.config import settings as _settings  # noqa: E402
from app.services import agentops as _agentops  # noqa: E402
from app.routes import dev as _devroutes  # noqa: E402

print("— پل دسترسی عامل: گارد در حالت تجاری (dev خاموش) —")
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
    print("— حذف کامل پل exec (v0.3.11 — دستور مالک) —")
    check("سرویس exec از agentops حذف شده (run_host/run_in_core نبود)",
          not hasattr(_agentops, "run_host") and not hasattr(_agentops, "run_in_core"))
    check("پنجرهٔ موقت عبور هم حذف شده (_OPEN_EXEC_UNTIL و _exec_open_window نبود)",
          not hasattr(_devroutes, "_OPEN_EXEC_UNTIL")
          and not hasattr(_devroutes, "_exec_open_window"))
    r = c.post("/api/dev/exec", json={"cmd": "echo hi"})
    check("exec بدون توکن → 404 (مسیر حذف شده)", r.status_code == 404)
    r = c.post("/api/dev/exec", json={"cmd": "echo hi"}, headers=DEVH)
    check("exec حتی با توکن dev → 404 (مسیر واقعاً حذف شده)", r.status_code == 404)
    r = c.get("/api/dev/report", headers=DEVH)
    check("report با توکن dev → 200 (بقیهٔ پل‌ها پابرجا)", r.status_code == 200
          and "app" in (r.json() if r.status_code == 200 else {}))

    print("— نیم‌سرور DNS: درس‌های میدانی v0.3.12 (ترکیب compose) —")
    _dns_block = compose_src[compose_src.index("  dns-init:"):]
    _dns_svc = _dns_block[_dns_block.index("  dns:\n"):]
    _dns_svc = _dns_svc[:_dns_svc.index("  # ----------")]
    check("متغیرهای shell در اسکریپت dns دوباره-دلار شده‌اند ($$KEY — وگرنه interpolate خالی)",
          "$$KEY" in _dns_svc and '"$KEY"' not in _dns_svc)
    check("متغیرهای واقعی compose تک-$ مانده‌اند (KEY از PARASTOO_DNS_API_KEY)",
          'KEY="${PARASTOO_DNS_API_KEY:-}"' in _dns_svc)
    check("نام تنظیم master در PDNS 4.9 → primary اصلاح شده (Fatal: unknown setting)",
          "--primary=yes" in _dns_svc and "--master=yes" not in _dns_svc)
    check("بایند ۵۳ برای سرورهای systemd-resolved قابل تنظیم است (PARASTOO_DNS_BIND_IP)",
          "${PARASTOO_DNS_BIND_IP:-0.0.0.0}:53:53" in _dns_svc
          and "${PARASTOO_DNS_BIND_IP:-0.0.0.0}:53:53/udp" in _dns_svc)
    check("healthcheck با controlsocket است (wget/curl در ایمیج pdns وجود ندارد)",
          'test: ["CMD-SHELL", "test -S /var/run/pdns/pdns.controlsocket || exit 1"]' in _dns_svc)
    check("dns-init مالکیت volumeها را به uid کاربر pdns می‌دهد و dns پس از تکمیلش بالا می‌آید",
          'user: "0:0"' in _dns_block and "$$(id -u pdns)" in _dns_block
          and "condition: service_completed_successfully" in _dns_svc)

    print("— پل دسترسی عامل: گارد توکن —")
    r = c.get("/api/dev/netcheck?url=ftp://x.example", headers=DEVH)
    check("netcheck با scheme نامعتبر → 400", r.status_code == 400)
    r = c.get("/api/dev/netcheck?groups=nogroup", headers=DEVH)
    check("netcheck با گروه ناشناخته → 400", r.status_code == 400)

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
        # درس میدانی v0.3.13 (zeolitecenter.ir): پاوردی‌ان‌اس واقعی محتوای NS بدون
        # نقطهٔ پایانی را با «Not in expected format» رد می‌کند؛ ماک قبلی این را نمی‌دید.
        _ns_bad = [r["content"] for z in _pzones.values() for rr in z["rrsets"]
                   if rr["type"] == "NS" for r in rr.get("records", [])
                   if not str(r["content"]).endswith(".")]
        check("محتوای NS در provision مطلق است (FQDN با نقطهٔ پایانی)", not _ns_bad)

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
    check("فایل audit ساخته شد و رکورد netcheck/sql دارد", _audit.is_file()
          and any(_json.loads(l)["kind"] in ("netcheck", "sql")
                  for l in _audit.read_text().splitlines() if l.strip()))
    check("رکورد audit عملیات‌های DNS هم ثبت شده", any(_json.loads(l)["kind"] == "dns"
          for l in _audit.read_text().splitlines() if l.strip()))
finally:
    _settings.DEV_MODE, _settings.DEV_TOKEN = _old_dev, _old_token

print("— درس میدانی v0.3.16: ایمیل ACME + نظافت پوشهٔ داینامیک —")
import tempfile as _tmpmod  # noqa: E402

from app.config import settings as _cfg  # noqa: E402
from app.validation import acme_email_error  # noqa: E402
from app.traefik_ops import clean_dynamic_dir  # noqa: E402

check("ایمیل عمومی سالم → None", acme_email_error("rulingcode@gmail.com") is None)
check("ایمیل بدون @ → email_invalid", acme_email_error("mohsen") == "email_invalid")
check("ایمیل دامنهٔ .local → email_nonpublic_domain",
      acme_email_error("mohsen@parastoo.local") == "email_nonpublic_domain")
check("ایمیل دامنهٔ .localhost → email_nonpublic_domain",
      acme_email_error("a@b.localhost") == "email_nonpublic_domain")

_dyn = Path(_tmpmod.mkdtemp(prefix="parastoo-dyn-")) / "dynamic"
_dyn.mkdir(parents=True)
(_dyn / "routers.yml").write_text("http: {}")
(_dyn / ".parastoo-sync.yml").write_text("junk")
(_dyn / "w.tmp").write_text("junk")
(_dyn / "custom.yml").write_text("http:\n  routers: {}\n")
_old_cfg = _cfg.CONFIG_DIR
try:
    _cfg.CONFIG_DIR = str(_dyn.parent)
    check("فایل پنهان و موقت حذف شد",
          sorted(clean_dynamic_dir()) == [".parastoo-sync.yml", "w.tmp"])
    check("routers.yml دست‌نخورده ماند", (_dyn / "routers.yml").is_file())
    check("yml آشکارِ مالک حذف نشد", (_dyn / "custom.yml").is_file())
    check("دور دوم چیزی برای حذف ندارد", clean_dynamic_dir() == [])
    _cfg.CONFIG_DIR = "/tmp/parastoo-no-such-dir-xyz"
    check("پوشهٔ ناموجود → بدون خطا", clean_dynamic_dir() == [])
finally:
    _cfg.CONFIG_DIR = _old_cfg

print("— درس میدانی v0.3.17: ریشه/www بی‌روتر مانده بودند —")
import yaml as _yaml  # noqa: E402

from app.traefik_ops import render_dynamic as _rd  # noqa: E402

_y = _rd("panel.example.com", "git.example.com", True, base_domain="example.com")
check("روترهای ریشه/www با گواهی اختصاصی در حالت le (۴ گواهی، بدون دام‌الگو)",
      "Host(`example.com`" in _y and _y.count("certResolver: le") == 4
      and "apps-wild" not in _y)
check("روتر www جدا — گواهی مستقل از ریشه", "Host(`www.example.com`)" in _y)
check("ری‌دایرکت به پنل با حفظ مسیر", "https://panel.example.com/$2" in _y
      and "noop@internal" in _y and "(www\\.)?example\\.com" in _y)
_d = _yaml.safe_load(_y)
check("خروجی YAML سالم — ساختار روتر/میدل‌ویر",
      len(_d["http"]["routers"]) == 4
      and _d["http"]["routers"]["base-redir"]["middlewares"] == ["base-redir-mw"]
      and _d["http"]["routers"]["base-redir"]["service"] == "noop@internal"
      and _d["http"]["middlewares"]["base-redir-mw"]["redirectRegex"]["permanent"] is False)
_y2 = _rd("panel.example.com", "git.example.com", False, base_domain="example.com")
check("بدون TLS: هدف http و بدون گواهی",
      _yaml.safe_load(_y2)["http"]["middlewares"]["base-redir-mw"]["redirectRegex"]
      ["replacement"] == "http://panel.example.com/$2" and "certResolver" not in _y2)
check("بدون دامنهٔ پایه: روتر ریشه ساخته نمی‌شود",
      "noop@internal" not in _rd("panel.example.com", "git.example.com", True))

print("— الگوی ساب‌دامنهٔ بی‌نهایت (v0.3.33): وایلدکارد DNS-01 روی PDNS خودمان —")
from app.traefik_ops import render_static as _rs  # noqa: E402
from app import dockerops as _ddo  # noqa: E402
from app.config import settings as _st  # noqa: E402
_s = _rs(True, "a@b.ir")
check("رزولور wild با چالش DNS-01 در استاتیک هسته",
      "dnsChallenge" in _s and "provider: pdns" in _s)
_wild = _s.split("  wild:")[1]
check("رزولور wild همیشه لت‌انکریپت است (زروسsl شناسهٔ .ir را رد می‌کند)",
      "caServer" not in _wild and "eab" not in _wild and "zerossl" not in _wild)
check("رزولور le با HTTP-01 باقی است — خرابی DNS-01 زیرساخت را نمی‌بندد",
      "httpChallenge" in _s.split("  wild:")[0])
_o_ca, _o_kid, _o_h = _st.ACME_CA, _st.ACME_EAB_KID, _st.ACME_EAB_HMAC
_st.ACME_CA, _st.ACME_EAB_KID, _st.ACME_EAB_HMAC = "zerossl", "kid-1", "hmac-1"
_z = _rs(True, "a@b.ir")
_st.ACME_CA, _st.ACME_EAB_KID, _st.ACME_EAB_HMAC = _o_ca, _o_kid, _o_h
check("ویزارد دیگر EAB زروسsl را نمی‌بُرد (گپ v0.3.30)",
      'caServer: "https://acme.zerossl.com/v2/DV90"' in _z
      and 'kid: "kid-1"' in _z and 'hmacEncoded: "hmac-1"' in _z)
check("وایلدکارد در حالت زروسsl هم لت‌انکریپت می‌ماند",
      "caServer" not in _z.split("  wild:")[1]
      and "kid:" not in _z.split("  wild:")[1])
_y3 = _rd("panel.example.com", "git.example.com", True, base_domain="example.com",
          wild_mode=True)
_d3 = _yaml.safe_load(_y3)
_aw = _d3["http"]["routers"].get("apps-wild", {})
check("حالت وایلدکارد: دام‌الگوی سایت‌ها (قاعده + دامنهٔ main + رزولور wild)",
      _aw.get("rule") == "HostRegexp(`{any:.+}.apps.example.com`)"
      and _aw.get("tls", {}).get("domains", [{}])[0].get("main") == "*.apps.example.com"
      and _aw.get("tls", {}).get("certResolver") == "wild")
check("دام‌الگو اولویت ۱ دارد و ترافیک سایت‌ها را نمی‌رباید",
      _aw.get("priority") == 1)
check("بدون TLS دام‌الگو هم نیست", "HostRegexp" not in _y2)
check("حالت وایلدکارد: پنل/گیت روی wild با اولویت ۱۰۰ (از روترهای لیبلی ببرند)",
      _d3["http"]["routers"]["panel-dyn"].get("priority") == 100
      and _d3["http"]["routers"]["panel-dyn"]["tls"]["certResolver"] == "wild"
      and _d3["http"]["routers"]["panel-dyn"]["tls"]["domains"][0]["main"] == "*.example.com"
      and _d3["http"]["routers"]["git-dyn"]["tls"]["domains"][0]["main"] == "*.example.com")
check("حالت وایلدکارد: ریشه گواهی apex جدا (وایلدکارد یک برچسب را می‌پوشاند)",
      _d3["http"]["routers"]["base-redir"]["tls"]["domains"][0]["main"] == "example.com"
      and _d3["http"]["routers"]["base-redir"].get("priority") == 100
      and _d3["http"]["routers"]["www-redir"]["tls"]["domains"][0]["main"] == "*.example.com")
check("حالت وایلدکارد: هیچ سفارش le باقی نمی‌ماند",
      "certResolver: le" not in _y3 and len(_d3["http"]["routers"]) == 5)
check("حالت le (nip.io/دامنهٔ بیرونی): دقیقاً رفتار قدیم — بدون دام‌الگو و اولویت",
      "apps-wild" not in _y and "priority" not in _y
      and _rd("p.nip.io", "g.nip.io", True).count("certResolver: le") == 2)
_oltls = _ddo.tls_enabled_now
_olw = _ddo.wildcard_mode_now
_ddo.tls_enabled_now = lambda: True
_ddo.wildcard_mode_now = lambda db=None: True
_l = _ddo._site_labels("shop", "shop.apps.example.com", 8080)
_ddo.tls_enabled_now = _oltls
_ddo.wildcard_mode_now = _olw
_r = "traefik.http.routers.parastoo-site-shop"
check("لیبل سایت در حالت وایلدکارد: رزولور wild + وایلدکارد مشتق‌شده از خود subdomain",
      _l.get(f"{_r}.tls.certresolver") == "wild"
      and _l.get(f"{_r}.tls.domains[0].main") == "*.apps.example.com")
_ddo.tls_enabled_now = lambda: True
_ddo.wildcard_mode_now = lambda db=None: False
_l4 = _ddo._site_labels("shop", "shop.apps.example.com", 8080)
_ddo.tls_enabled_now = _oltls
_ddo.wildcard_mode_now = _olw
check("لیبل سایت بیرون از حالت وایلدکارد: مسیر قدیمی le (nip.io/دامنهٔ بیرونی)",
      _l4.get(f"{_r}.tls.certresolver") == "le"
      and not any(k.endswith("domains[0].main") for k in _l4))
_ddo.tls_enabled_now = lambda: False
_l2 = _ddo._site_labels("shop", "shop.apps.example.com", 8080)
_ddo.tls_enabled_now = _oltls
check("بدون TLS: بدون certresolver (رفتار قدیمی)",
      not any("certresolver" in k for k in _l2))
_c = (ROOT / "docker-compose.yml").read_text()
check("ترافیک: DNS-01 روی PDNS خودمان با کلید از dns_meta (پسوند _FILE)",
      "PDNS_API_URL: http://dns:8081" in _c
      and "PDNS_API_KEY_FILE: /meta/api-key" in _c and "dns_meta:/meta:ro" in _c)
check("ترافیک پس از سالم‌بودن dns بالا می‌آید (فایل کلید آماده)",
      "condition: service_healthy" in _c)
check("هسته: CA/EAB از .env به هسته می‌رسد",
      "PARASTOO_ACME_CA: ${PARASTOO_ACME_CA:-}" in _c
      and "PARASTOO_ACME_EAB_KID" in _c and "PARASTOO_ACME_EAB_HMAC" in _c)
_t = (ROOT / "deploy" / "templates" / "traefik.yml.tmpl").read_text()
check("قالب نصب‌کننده هم رزولور wild دارد (همگام با هسته)",
      "dnsChallenge" in _t and "provider: pdns" in _t and "  wild:" in _t)

print("— همگام‌ساز پیکربندی ترافیک: استقرار بدون نصب مجدد (v0.3.33) —")
import tempfile as _tf  # noqa: E402
_ensure_dir = Path(_tf.mkdtemp()) / "config"
(_ensure_dir / "dynamic").mkdir(parents=True)
(_ensure_dir / "traefik.yml").write_text("old-static-no-wild\n", encoding="utf-8")
(_ensure_dir / "dynamic" / "routers.yml").write_text("old-dyn\n", encoding="utf-8")
from app import domains as _dom  # noqa: E402
from app import traefik_ops  # noqa: E402
_o_dom = {n: getattr(_dom, n) for n in ("base", "panel", "git", "tls",
                                        "acme_email", "wildcard_mode")}
_dom.base = lambda db=None: "example.com"
_dom.panel = lambda db=None: "panel.example.com"
_dom.git = lambda db=None: "git.example.com"
_dom.tls = lambda db=None: True
_dom.acme_email = lambda db=None: "ops@example.com"
_dom.wildcard_mode = lambda db=None: True
_old_cfg2 = _st.CONFIG_DIR
_st.CONFIG_DIR = str(_ensure_dir)
_eres = traefik_ops.ensure_traefik_config(None)
check("فایل‌های رندرشدهٔ کهنه بازنویسی می‌شوند (استقرار بدون نصب مجدد)",
      _eres.get("static") is True and _eres.get("dynamic") is True)
check("استاتیک پس از همگام‌سازی رزولور wild دارد",
      "provider: pdns" in (_ensure_dir / "traefik.yml").read_text(encoding="utf-8"))
check("داینامیک پس از همگام‌سازی دام‌الگوی وایلدکارد دارد",
      "apps-wild" in (_ensure_dir / "dynamic" / "routers.yml").read_text(encoding="utf-8"))
check("دور دوم همگام‌سازی هیچ چیز بازنویسی نمی‌کند (ایدمپوتنت)",
      (lambda r: r.get("static") is False and r.get("dynamic") is False)(
          traefik_ops.ensure_traefik_config(None)))
for n, f in _o_dom.items():
    setattr(_dom, n, f)
_st.CONFIG_DIR = _old_cfg2
print("— حالت‌سنج wildcard_mode (v0.3.34) —")
from app.services import dnsops as _dn  # noqa: E402
_o_av, _o_gz = _dn.available, _dn.get_zone
_o_base = _dom.base
_dom.base = lambda db=None: "example.com"
_dn.available = lambda: True
_dn.get_zone = lambda z: {"name": z}
check("زون روی PDNS خودمان → حالت وایلدکارد", _dom.wildcard_mode(None) is True)
_dn.get_zone = lambda z: (_ for _ in ()).throw(RuntimeError("404"))
check("زون نزد ما نیست → مسیر قدیمی le", _dom.wildcard_mode(None) is False)
_dn.available = lambda: False
check("PDNS در دسترس نیست → مسیر قدیمی le", _dom.wildcard_mode(None) is False)
_dom.base = lambda db=None: "1-2-3-4.nip.io"
_dn.available = lambda: True
_dn.get_zone = lambda z: {"name": z}
check("nip.io هرگز حالت وایلدکارد نمی‌گیرد (چالش DNS-01 ناممکن)",
      _dom.wildcard_mode(None) is False)
_dom.base = _o_base
_dn.available, _dn.get_zone = _o_av, _o_gz
_w = (ROOT / "core" / "app" / "workers.py").read_text()
check("heal فاز صفر: همگام‌سازی پیکربندی پیش از همهٔ فازها (بدون استثنا)",
      "ensure_traefik_config(db0)" in _w
      and _w.index("ensure_traefik_config(db0)") < _w.index("gitea_stale()"))
_c2 = (ROOT / "core" / "app" / "config.py").read_text()
check("هسته: ایمیل ACME هم از env می‌آید (رندر همگام با نصب‌کننده)",
      "ACME_EMAIL: ${ACME_EMAIL}" in _c
      and 'ACME_EMAIL: str = os.environ.get("ACME_EMAIL"' in _c2)

print("— درس میدانی v0.3.18: EADDRINUSE پورت ۵۳ در نصب تازه —")
_ins = (ROOT / "install.sh").read_text()
check("install.sh گوش‌دادن resolved را با پسوند اسکوپ %lo هم می‌بیند (v0.3.19)",
      "127\\.0\\.0\\.5[34](%[a-zA-Z0-9._-]+)?:53" in _ins and "ss -tln" in _ins)
check("بایند DNS خودکار به IP عمومی می‌رود و در .env نوشته می‌شود",
      "PARASTOO_DNS_BIND_IP=\"${PUBLIC_IP}\"" in _ins
      and "PARASTOO_DNS_BIND_IP=${PARASTOO_DNS_BIND_IP}" in _ins)
check("صریح‌بودن env بر تشخیص خودکار مقدم است",
      "-z \"${PARASTOO_DNS_BIND_IP:-}\"" in _ins)

print("— درس میدانی v0.3.19: بازسازی سرور — IP خروجی NAT به‌جای IP ورودی —")
check("IP خروجی که روی اینترفیسی نیست به IP ورودیِ اینترفیص اصلاح می‌شود",
      "INBOUND_IP=" in _ins
      and "Auto-correcting to the interface (inbound) IP" in _ins
      and 'PUBLIC_IP="$INBOUND_IP"' in _ins)
check("کاندیدهای IP اینترفیص فقط عمومی‌اند (خصوصی/بریج داکر/لینک-لوکال حذف)",
      "grep -vE '^(10\\.|127\\.|169\\.254\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\." in _ins)
check("مسیر چند-IP: بجای اصلاح خودکار، دستور صریح PARASTOO_PUBLIC_IP می‌دهد",
      "Multiple interface IPs" in _ins and "PARASTOO_PUBLIC_IP=<real-IP>" in _ins)

print("— درس میدانی v0.3.20: هشدار کاذب «Stack failed to start» + بیمهٔ گواهی —")
check("شکست compose up قبل از die، پروب واقعی سلامت می‌کند (مسابقهٔ health-check)",
      "UP_PROBE" in _ins
      and "probing whether the stack converged anyway" in _ins
      and "health-check race; continuing" in _ins
      and "core does not answer /healthz" in _ins)
check("در مسیر پروب، up بدون build یک‌بار تکرار می‌شود (خطاهای گذرا)",
      "docker compose up -d >/dev/null 2>&1 || true" in _ins)
check("نصب‌کننده پیش از استقرار گواهی را از فروشگاه ماندگار بازیابی می‌کند (قانون v0.3.32)",
      "persistent TLS store" in _ins
      and "tls-store.sh\" ensure" in _ins
      and _ins.index("tls-store.sh") < _ins.index('mark "5) docker compose up"'))
_bk = (ROOT / "bin" / "parastoo").read_text()
check("parastoo backup گواهی‌ها را از فروشگاه ماندگار پشتیبان می‌گیرد (نه مسیر مردهٔ قدیمی)",
      'backups/acme-${TS}.json' in _bk and "/var/lib/parastoo/tls/acme.json" in _bk)

print("— میدان v0.3.22: خودترمیم گیتا (realm کهنهٔ رجیستری) + دستورهای push —")
_tpl = (ROOT / "core" / "app" / "web" / "templates" / "project_detail.html").read_text()
check("دستور مخزن کلون‌محور است (مخزن seed شده؛ git init کاربر را به تعارض و fetch-first می‌اندازد)",
      "git clone " in _tpl and "git remote add origin" not in _tpl and "git init" not in _tpl)
check("آدرس ریموت با TLS فعال https می‌شود (git بعد از ۳۰۱ http→https هدر Auth را حذف می‌کند)",
      "http{{ 's' if domains.tls() else '' }}://" in _tpl
      and "http://{{ user.gitea_username }}" not in _tpl)
_g = (ROOT / "core" / "app" / "gitea.py").read_text()
check("seed گروهی گیتا پیام کامیت فارسی می‌فرستد (پیام پیش‌فرض انگلیسی «Add …» نباشد)",
      'json={"message": message,' in _g)
_d = (ROOT / "core" / "app" / "dockerops.py").read_text()
check("gitea_stale env کانتینر را با دامنهٔ فعال می‌سنجد؛ داکر ناموجود → کاری ندارد",
      "def gitea_stale" in _d and 'return (False, "")' in _d)
check("recreate_gitea همهٔ ویژگی‌ها را از inspect حفظ می‌کند (لیبل‌های compose، healthcheck، alias شبکه)",
      "com.docker.compose" in _d and "create_host_config" in _d
      and "create_endpoint_config" in _d
      and "GITEA__server__ROOT_URL={gitea_root_url()}" in _d)
check("شکست رجیستری پیام فارسی گویا دارد + ریشهٔ رایج (ROOT_URL کهنه) را می‌گوید",
      "انتشار ایمیج در رجیستری داخلی ناموفق بود" in _d and "404" in _d)
import tempfile  # noqa: E402
from app.config import settings as _settings  # noqa: E402
_henv = Path(tempfile.mkdtemp()) / ".env"
_henv.write_text("A=1\nGITEA_ROOT_URL=http://git.old-nip.nip.io/\nB=2\n", encoding="utf-8")
_old_henv = _settings.HOST_ENV_FILE
_settings.HOST_ENV_FILE = str(_henv)
_ok = dockerops.update_host_env({"GITEA_ROOT_URL": "https://git.example.ir/"})
_txt = _henv.read_text(encoding="utf-8")
check("update_host_env کلید موجود را همان‌جا جایگزین می‌کند (بقیهٔ خط‌ها دست‌نخورده)",
      _ok and "GITEA_ROOT_URL=https://git.example.ir/" in _txt
      and "A=1" in _txt and "B=2" in _txt and "old-nip" not in _txt)
_ok2 = dockerops.update_host_env({"NEW_KEY": "v1"})
check("update_host_env کلید غایب را به انتها اضافه می‌کند",
      _ok2 and "NEW_KEY=v1" in _henv.read_text(encoding="utf-8")
      and "GITEA_ROOT_URL=https://git.example.ir/" in _henv.read_text(encoding="utf-8"))
_settings.HOST_ENV_FILE = _old_henv
check("update_host_env بدون فایل ساکت False می‌دهد (هرگز استثنا نمی‌دهد)",
      dockerops.update_host_env({"X": "y"}) is False)
_w = (ROOT / "core" / "app" / "workers.py").read_text()
check("ورکر heal هر ۱۲۰ ثانیه گیتای ناهم‌خوان با دامنه را بازسازی می‌کند",
      "HEAL_INTERVAL = 120" in _w and "def heal_once" in _w
      and "args=(heal_once, HEAL_INTERVAL)" in _w and "sync_host_env" in _w)
_t = (ROOT / "core" / "app" / "traefik_ops.py").read_text()
check("apply_domain خودش هم .env و هم گیتا را هم‌خوان می‌کند",
      "sync_host_env" in _t and "recreate_gitea" in _t and "gitea_stale" in _t)
_cm = (ROOT / "docker-compose.yml").read_text()
check(".env هاست داخل هسته سوار می‌شود (همگام‌سازی ماندگار برای up بعدی compose)",
      "- ./.env:/app/host-env/.env" in _cm)

print("— میدان v0.3.26: provision زون پایه در نصب‌کننده (پایان مرغ‌وتخم‌مرغ دامنهٔ صریح) —")
_to = (ROOT / "core" / "app" / "traefik_ops.py").read_text()
check("site_cert_ok دست‌دادن کامل TLS با SNI هاست می‌سنجد؛ خطای تأیید یعنی گواهی نیست",
      "def site_cert_ok" in _to and "server_hostname=h" in _to
      and "SSLCertVerificationError" in _to)
check("site_cert_ok ورودی خالی و ترافیکِ در-دسترس-نیست را «قابل تصمیم‌گیری نیست» می‌دهد",
      "return None" in _to and _to.count("return None") >= 3)
_w = (ROOT / "core" / "app" / "workers.py").read_text()
check("ورکر heal فاز گواهی دارد: DNS گارد + فاصلهٔ حداقل ری‌استارت ترافیک",
      "_tls_certs_heal" in _w and "TLS_RESTART_GAP = 600" in _w
      and "resolve_a(h) != ip" in _w
      and "Site.status == \"running\"" in _w)
check("heal گواهی زیرساخت (پنل/گیت/پایه/www) را هم پوشش می‌دهد نه فقط سایت‌ها — میدان v0.3.29: پس از ۴۲۹ لایهٔ LE پنل برای همیشه روی گواهی پیش‌فرض می‌ماند",
      "settings.PANEL_DOMAIN" in _w and "settings.GIT_DOMAIN" in _w
      and 'www.{(settings.BASE_DOMAIN' in _w and "_consider(host)" in _w)
check("استثنای فاز گیتا در heal فاز گواهی را قربانی نمی‌کند — return زودهنگام پایان یافت (میدان v0.3.29: سه چرخه پشت‌سرهم گواهی غایب پنل را ندیدند)",
      "gitea staleness check failed (continuing)" in _w
      and "stale = False" in _w
      and _w.index("stale = False") < _w.index("_tls_certs_heal(db)"))
check("بیلد مردودِ رجیستری-گواهی پس از آماده‌شدن گواهی خودکار دوباره صف می‌شود (فاز ج heal — میدان v0.3.29: پنجرهٔ انتظار گواهی هر بیلد را می‌کشت)",
      "_build_retry_heal" in _w and "_registry_tls_failure" in _w
      and "REGISTRY_RETRY_GAP = 900" in _w
      and '"[auto-retry]" in (latest.log or "")' in _w
      and "site_cert_ok(domains.git()) is not True" in _w)
check("شکست push در پنجرهٔ انتظار گواهی پیام صادقانهٔ فارسی دارد نه خطای مبهم داکر",
      "گواهی HTTPS دامنهٔ رجیستری (گیت) هنوز صادر نشده" in _w
      and "نیازی به کاری نیست" in _w)
_inst = (ROOT / "install.sh").read_text()
check("نصب‌کننده ریدایرکت :80→:443 را مسیر سالم می‌داند — پایان آژیر کاذب ۳۰۱ روی هر نصب سالمِ TLS-دار (میدان v0.3.29)",
      "301|302|307|308)" in _inst
      and '--resolve "$PANEL_DOMAIN:443:127.0.0.1"' in _inst)
check("نصب‌کننده ۴۲۹ لایهٔ LE را صادقانه اسم می‌برد نه «خرابی در لایهٔ ترافیک»",
      "rateLimited" in _inst and "Let's Encrypt rate limit" in _inst
      and "no action needed" in _inst)
_tmpl = (ROOT / "deploy" / "templates" / "traefik.yml.tmpl").read_text()
check("CA ACME قابل‌انتخاب است: نگاشت caServer و EAB در قالب ترافیک — پایان انحصار لت‌انکریپت (میدان 2026-11-06: سقف نرخ ۴۲۹ پنجمین نصب تازهٔ همان دامنه را می‌بست و ZeroSSL از ایران سبز و سریع است)",
      "__ACME_CASERVER__" in _tmpl and "__ACME_EAB__" in _tmpl
      and "acme.zerossl.com/v2/DV90" in _inst
      and "eab-credentials" in _inst
      and "PARASTOO_ACME_CA" in _inst
      and "PARASTOO_ACME_EAB_KID" in _inst)
check("دایرکتوری CA پیش از نصب پروب می‌شود — CA مسدود (میدان: BuyPass/Actalis از ایران تایم‌اوت) همان‌جا صادقانه دیده می‌شود نه ساعت‌ها بعد در لاگ ترافیک",
      "ACME CA directory" in _inst and "docs/SANCTIONS.md" in _inst
      and "_ACME_CODE" in _inst)
_doc = (ROOT / "scripts" / "doctor.sh").read_text()
check("doctor.sh برای ریدایرکت ۳۰۱ مسیر HTTPS را هم می‌سنجد و تفسیر می‌کند",
      "301|302|307|308)" in _doc and '--resolve "$PANEL:443:127.0.0.1"' in _doc)
_r = (ROOT / "core" / "app" / "routes" / "projects.py").read_text()
check("انتشار وقتی گواهی آماده نیست پیام صادقانه می‌دهد نه سبزِ دروغین",
      "published_cert_pending" in _r and "site_cert_ok(site.subdomain) is False" in _r)
check("پیام published_cert_pending در FLASHES ثبت است",
      '"published_cert_pending":' in (ROOT / "core" / "app" / "tpl.py").read_text())
_sd = (ROOT / "core" / "app" / "web" / "templates" / "setup_domain.html").read_text()
check("ویزارد پس از فعال‌سازی موفق دامنه خروج به داشبورد دارد",
      "domain_applied" in _sd and "برو به داشبورد" in _sd and 'href="/dashboard"' in _sd)

print()
print("— قانون گواهی ماندگار (v0.3.32) —")
_i32 = (ROOT / "install.sh").read_text()
_r32 = (ROOT / "scripts" / "factory-reset.sh").read_text()
_t32 = (ROOT / "scripts" / "tls-store.sh").read_text()
_u32 = (ROOT / "uninstall.sh").read_text()
_c32 = (ROOT / "bin" / "parastoo").read_text()
check("فروشگاه ماندگار با مسیر قانونی /var/lib/parastoo/tls و دستورهای save/restore/ensure/status وجود دارد",
      "/var/lib/parastoo/tls" in _t32 and "acme.json" in _t32
      and "cmd_save" in _t32 and "cmd_restore" in _t32 and "cmd_ensure" in _t32 and "cmd_status" in _t32)
check("نصب‌کننده پیش از آوردن استک از فروشگاه ensure می‌کند (هرگز درخواست تکراری به CA)",
      _i32.count("tls-store.sh") >= 3
      and _i32.index("tls-store.sh") < _i32.index('mark "5) docker compose up"'))
check("پس از استقرار هم فروشگاه دوباره ensure+save می‌شود (ولوم تازهٔ پس از ریست)",
      _i32.rfind("tls-store.sh") > _i32.rindex('mark "6d'))
check("نصب‌کننده acme.json قدیمی مسیر مرده را هم به فروشگاه می‌پذیرد (legacy adoption)",
      "config/letsencrypt/acme.json" in _i32 and "PARASTOO_TLS_LIVE=" in _i32)
check("factory-reset پیش از پاک‌کردن ولوم‌ها گواهی را در فروشگاه ذخیره می‌کند",
      _r32.index("tls-store.sh") < _r32.index("down -v"))
check("factory-reset پس از استقرار، گواهی را در ولوم تازه بازیابی می‌کند (ensure)",
      "tls-store.sh\" ensure" in _r32
      and _r32.index("tls-store.sh\" ensure") > _r32.index("down -v"))
check("factory-reset فروشگاه را پاک نمی‌کند و وضعیتش را گزارش می‌دهد (قانون: هرگز)",
      "never wiped" in _r32 and "tls-store.sh\" status" in _r32)
check("uninstall فروشگاه را عمداً نگه می‌دارد و تایمر همگام‌سازی را جمع می‌کند",
      "/var/lib/parastoo/tls" in _u32 and "parastoo-tls-store.timer" in _u32)
check("تایمر همگام‌سازی گواهی (هر ۱۰ دقیقه) نصب و فعال می‌شود",
      "parastoo-tls-store.timer" in _i32
      and "enable --now parastoo-tls-store.timer" in _i32
      and (ROOT / "deploy/systemd/parastoo-tls-store.timer").exists()
      and (ROOT / "deploy/systemd/parastoo-tls-store.service").exists())
check("پشتیبان CLI اول فروشگاه را همگام و بعد آرشیو می‌کند",
      "tls-store.sh" in _c32 and "/var/lib/parastoo/tls/acme.json" in _c32)
check("فروشگاه هرگز فروشگاه سالم را با فایل خراب نمی‌پوشاند و JSON را اعتبارسنجی می‌کند",
      "_json_valid" in _t32 and "refusing to archive garbage" in _t32)
check("قانون در قرارداد حلقهٔ ایجنت ثبت است (AI_AGENT_LOOP §3 و §0)",
      "قانون گواهی ماندگار" in (ROOT / "docs/AI_AGENT_LOOP.md").read_text())

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
