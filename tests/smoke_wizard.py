"""تست دودی ویزارد — توکن نصب، گام‌ها، دامنه و خروجی‌های ترافیک."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.update(
    DATABASE_URL="sqlite:////tmp/parastoo-wizard.db",
    PARASTOO_DISABLE_WORKERS="1",
    SECRET_KEY="wizard-test-secret",
    BASE_DOMAIN="1-2-3-4.nip.io",
    TLS_ENABLED="false",
    PARASTOO_SETUP_TOKEN="aaaa-bbbb-cccc",
    PARASTOO_PUBLIC_IP="203.0.113.10",
    PARASTOO_CONFIG_DIR="/tmp/parastoo-test-config",
)
if os.path.exists("/tmp/parastoo-wizard.db"):
    os.remove("/tmp/parastoo-wizard.db")

sys.path.insert(0, str(ROOT / "core"))

from fastapi.testclient import TestClient  # noqa: E402

from app import domains, security, traefik_ops, workers  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Setting  # noqa: E402

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
    print("— گام ۱: توکن نصب —")
    r = c.get("/setup")
    check("بدون توکن → فرم توکن نشان داده می‌شود",
          r.status_code == 200 and "توکن نصب" in r.text)
    r = c.post("/setup/token", data={"token": "zzzz-yyyy-xxxx"}, follow_redirects=False)
    check("توکن غلط رد می‌شود", "m=bad_token" in r.headers.get("location", ""))
    r = c.post("/setup", data={"email": "x@x.ir", "password": "123456"}, follow_redirects=False)
    check("بدون عبور از گیت، ساخت مدیر هم رد می‌شود", "m=bad_token" in r.headers.get("location", ""))
    r = c.post("/setup/token", data={"token": " aaaa-bbbb-cccc "}, follow_redirects=False)
    check("توکن درست (با فاصله) قبول شد", r.status_code == 303 and "parastoo_gate" in r.cookies)
    r = c.get("/setup")
    check("بعد از گیت → فرم مدیر", r.status_code == 200 and "حساب مدیر" in r.text)

    print("— گام ۲: حساب مدیر —")
    r = c.post("/setup", data={"email": "admin@test.ir", "password": "secret1",
                               "name": "مدیر"}, follow_redirects=False)
    check("ساخت مدیر → ریدایرکت به گام دامنه",
          r.status_code == 303 and "/setup/domain" in r.headers["location"])
    check("کوکی نشست ست شد", "parastoo_session" in r.cookies)
    r = c.get("/setup/domain")
    check("صفحه دامنه باز می‌شود",
          r.status_code == 200 and "دامنه" in r.text and "1-2-3-4.nip.io" in r.text)
    r = c.get("/setup")
    check("ادمینِ واردشده /setup → صفحه دامنه",
          r.status_code == 303 and "/setup/domain" in r.headers["location"])
    c3 = TestClient(app, follow_redirects=False)
    r = c3.get("/setup")
    check("بازدیدکننده ناشناس /setup → login (ویزارد بسته)",
          r.status_code == 303 and "/login" in r.headers["location"])
    r = c.post("/setup/token", data={"token": "aaaa-bbbb-cccc"}, follow_redirects=False)
    check("توکن بعد از نصب دیگر کار نمی‌کند", r.status_code == 303 and "/login" in r.headers["location"])

    print("— گام ۳: بررسی DNS و اعمال دامنه —")
    r = c.get("/setup/domain/check.json?domain=bad_domain!")
    check("دامنه نامعتبر → error", r.json().get("error") == "invalid_domain")
    r = c.get("/setup/domain/check.json?domain=example.com")
    j = r.json()
    check("بررسی DNS معتبر → ساختار درست",
          r.status_code == 200 and isinstance(j.get("records"), list)
          and len(j["records"]) == 3 and j["records"][0]["host"] == "panel.example.com")
    check("رکوردها به IP سرور اشاره نمی‌کنند (طبیعی در تست)", j["ok"] is False)
    r = c.post("/setup/domain", data={"base_domain": "example.com", "tls": "on",
                                      "acme_email": "admin@example.com", "csrf": security.csrf_token(1)},
               follow_redirects=False)
    check("HTTPS بدون DNS نشسته → بن‌بست نیست؛ زمان‌بندی خودکار",
          "m=domain_scheduled" in r.headers.get("location", ""))
    db2 = SessionLocal()
    _prows = {s.key: s.value for s in db2.query(Setting).filter(Setting.key.like("domain_pending_%")).all()}
    db2.close()
    check("درخواست معلق در جدول Setting ثبت شد (base/tls/acme/since)",
          _prows.get("domain_pending_base") == "example.com"
          and _prows.get("domain_pending_tls") == "1"
          and _prows.get("domain_pending_acme") == "admin@example.com"
          and int(_prows.get("domain_pending_since") or 0) > 0)
    r = c.get("/setup/domain")
    check("صفحهٔ دامنه بنر انتظار را نشان می‌دهد",
          r.status_code == 200 and "فعال‌سازی خودکار در انتظار DNS" in r.text
          and "example.com" in r.text)
    r = c.post("/setup/domain", data={"base_domain": "example.com",
                                      "acme_email": "admin@example.com",
                                      "csrf": security.csrf_token(1)},
               follow_redirects=False)
    check("اعمال دامنه بدون HTTPS → موفق",
          "m=domain_applied" in r.headers.get("location", ""))
    db2 = SessionLocal()
    _left = db2.query(Setting).filter(Setting.key.like("domain_pending_%")).count()
    db2.close()
    check("اعمال موفق، انتظار معلق را خودکار پاک می‌کند", _left == 0)

    print("— نتیجه اعمال —")
    check("دامنه پنل پویا عوض شد", domains.panel() == "panel.example.com")
    check("دامنه گیت پویا عوض شد", domains.git() == "git.example.com")
    check("دامنه سایت‌ها پویا عوض شد", domains.apps() == "apps.example.com")
    check("TLS همچنان خاموش", domains.tls() is False)
    cfg = "/tmp/parastoo-test-config"
    check("فایل استاتیک ترافیک نوشته شد", os.path.exists(f"{cfg}/traefik.yml"))
    check("فایل داینامیک روترها نوشته شد", os.path.exists(f"{cfg}/dynamic/routers.yml"))
    dyn = open(f"{cfg}/dynamic/routers.yml").read()
    check("روتینگ داینامیک دامنه جدید را دارد",
          "panel.example.com" in dyn and "git.example.com" in dyn and "certResolver" not in dyn)
    static = open(f"{cfg}/traefik.yml").read()
    check("استاتیک بدون ریدایرکت (TLS خاموش)", "redirections" not in static)
    db = SessionLocal()
    rows = {s.key: s.value for s in db.query(Setting).filter(Setting.key.like("domain_%")).all()}
    db.close()
    check("تنظیمات در جدول Setting ذخیره شد",
          rows.get("domain_base") == "example.com" and rows.get("domain_tls") == "0")

    print("— الگوهای ترافیک (یونیت) —")
    s_on = traefik_ops.render_static(True, "a@b.ir")
    check("استاتیک TLS روشن: ریدایرکت + ایمیل",
          "redirections" in s_on and "a@b.ir" in s_on)
    try:
        import yaml
        yaml.safe_load(s_on)
        yaml.safe_load(traefik_ops.render_static(False, "a@b.ir"))
        yaml.safe_load(traefik_ops.render_dynamic("p.x.ir", "g.x.ir", True))
        yaml.safe_load(traefik_ops.render_dynamic("p.x.ir", "g.x.ir", False))
        check("همه خروجی‌های YAML معتبرند", True)
    except ImportError:
        print("  (pyyaml نصب نیست — اعتبارسنجی YAML رد شد)")

    print("— فعال‌سازی خودگردان: ورکر دامنه (v0.3.14) —")
    r = c.post("/setup/domain", data={"base_domain": "shop.example.net", "tls": "on",
                                      "acme_email": "tls@example.net",
                                      "csrf": security.csrf_token(1)},
               follow_redirects=False)
    check("زمان‌بندی دامنهٔ دوم با HTTPS",
          "m=domain_scheduled" in r.headers.get("location", ""))
    _real_check = traefik_ops.check_dns
    traefik_ops.check_dns = lambda base, ip: [
        {"label": x, "host": x, "expected": ip, "resolved": ip, "ok": True}
        for x in ("panel", "git", "wildcard")]
    try:
        workers.domain_once()
    finally:
        traefik_ops.check_dns = _real_check
    check("ورکر پس از نشستن DNS دامنه را با TLS اعمال کرد",
          domains.base() == "shop.example.net" and domains.tls() is True)
    db2 = SessionLocal()
    _left = db2.query(Setting).filter(Setting.key.like("domain_pending_%")).count()
    db2.close()
    check("اعمال توسط ورکر، انتظار را پاک کرد", _left == 0)
    dyn = open(f"{cfg}/dynamic/routers.yml").read()
    static = open(f"{cfg}/traefik.yml").read()
    check("روتینگ داینامیک دامنهٔ دوم با certResolver",
          "panel.shop.example.net" in dyn and "certResolver" in dyn)
    check("استاتیک TLS روشن: ریدایرکت + ایمیل ACME",
          "redirections" in static and "tls@example.net" in static)

    print("— فعال‌سازی خودگردان: انقضا و لغو —")
    domains.pending_set("late.example.org", "a@b.ir", True)
    db2 = SessionLocal()
    _row = db2.get(Setting, "domain_pending_since")
    _row.value = str(int(time.time()) - workers.PENDING_DEADLINE_S - 60)
    db2.commit()
    db2.close()
    domains._invalidate()  # تست مستقیم DB زده — کش ۵ ثانیه‌ای را باطل کن
    workers.domain_once()
    db2 = SessionLocal()
    _left = db2.query(Setting).filter(Setting.key.like("domain_pending_%")).count()
    db2.close()
    check("درخواست کهنهٔ بیش از ۷۲ ساعت پاک می‌شود و دامنهٔ فعال دست‌نخورده می‌ماند",
          _left == 0 and domains.base() == "shop.example.net")
    domains.pending_set("cancel.example.org", "a@b.ir", True)
    r = c.post("/setup/domain/pending/clear", data={"csrf": security.csrf_token(1)},
               follow_redirects=False)
    check("لغو دستی زمان‌بندی",
          "m=pending_cleared" in r.headers.get("location", "")
          and domains.pending_get() is None)

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
