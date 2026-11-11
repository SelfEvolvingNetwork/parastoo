"""تست دودی جفت‌سازی عامل پروژه (v0.5.27) — چرخهٔ کامل HTTP روی /api/agent/*.

پوشش (آینهٔ smoke_pairing پل ادمین، با دانهٔ پروژه):
  عامل:  POST /api/agent/claim {project, code} ← claim_id + claim_secret
  کاربر: POST /projects/<slug>/agent-approve (فرم پنل + CSRF)
  عامل:  POST /api/agent/claim/poll ← توکن pst_ ← GET /api/bridge/status

قرمزها: قالب/ضعف رمز، پروژهٔ ناموجود، CSRF فرم تأیید، دانه‌بندی پروژه در
تأیید (کاربر دیگر)، برداشت دوباره، رمز/راز غلط، انقضای پنجره‌ها، سقف‌ها،
فقط-هش روی دیسک، audit بدون مقدار رمز، متن دعوت بی‌رمز.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.update(
    DATABASE_URL="sqlite:////tmp/parastoo-agentpair-test.db",
    PARASTOO_DISABLE_WORKERS="1",
    SECRET_KEY="test-secret-key",
    BASE_DOMAIN="127.0.0.1.nip.io",
    TLS_ENABLED="false",
    REGISTER_OPEN="true",
    DATA_DIR="/tmp/parastoo-agentpair-test",
)
for p in ("/tmp/parastoo-agentpair-test.db",):
    if os.path.exists(p):
        os.remove(p)
import shutil
if os.path.isdir(os.environ["DATA_DIR"]):
    shutil.rmtree(os.environ["DATA_DIR"])

sys.path.insert(0, str(ROOT / "core"))

from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project  # noqa: E402
from app.services import agentpair as apair  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    cond = bool(cond)
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name} {extra}")


with TestClient(app, follow_redirects=False) as ca, \
        TestClient(app, follow_redirects=False) as cu, \
        TestClient(app, follow_redirects=False) as anon:
    print("— راه‌اندازی: میزبان + دو پروژه، کاربر دوم با پروژهٔ خودش —")
    r = ca.post("/setup", data={"email": "admin@test.ir", "password": "secret1",
                                "name": "میزبان"}, follow_redirects=False)
    check("ادمین ساخته شد", r.status_code == 303)
    admin_csrf = security.csrf_token(1)
    r = ca.post("/projects/new", data={"name": "اپ تست", "slug": "pair-app",
                                       "description": "", "csrf": admin_csrf},
                follow_redirects=False)
    check("پروژهٔ نخست ساخته شد", r.status_code == 303)

    r = cu.post("/register", data={"email": "other@test.ir", "password": "secret1",
                                   "name": "کاربر دیگر"}, follow_redirects=False)
    check("کاربر دوم ثبت‌نام شد", r.status_code == 303)
    other_csrf = security.csrf_token(2)
    r = cu.post("/projects/new", data={"name": "پروژهٔ دیگر", "slug": "other-app",
                                       "description": "", "csrf": other_csrf},
                follow_redirects=False)
    check("پروژهٔ دوم ساخته شد", r.status_code == 303)

    db = SessionLocal()
    proj = db.query(Project).filter(Project.slug == "pair-app").first()
    other = db.query(Project).filter(Project.slug == "other-app").first()
    db.close()

    print("— صفحهٔ پروژه: متن دعوت بی‌رمز —")
    r = ca.get("/projects/pair-app")
    check("صفحهٔ پروژه 200", r.status_code == 200)
    html = r.text
    check("کارت دعوت بدون توکن هست", "agent-invite-text" in html and "دعوت بدون توکن" in html)
    check("متن دعوت: slug و مسیر claim دارد", "pair-app" in html and "/api/agent/claim" in html)
    check("متن دعوت: هیچ pst_ خام در آن نیست", "PST_TOKEN" not in html.split("agent-invite-text")[1].split("</pre>")[0])
    check("فرم تأیید عامل هست", "/projects/pair-app/agent-approve" in html)

    inv = apair.invite_text("pair-app", "https://panel.example")
    check("invite_text: مسیر راهنما و پروژه دارد",
          "/docs/agent" in inv and "pair-app" in inv and "/api/agent/claim/poll" in inv)
    import re as _re
    check("invite_text: هیچ مقدار توکن واقعی در آن نیست",
          _re.search(r"pst_[A-Za-z0-9_\-]{16,}", inv) is None
          and _re.search(r"pat_[A-Za-z0-9_\-]{16,}", inv) is None
          and "apcsk_" not in inv)

    print("— قالب و ضعف رمز —")
    for bad in ("1234", "123456", "abcde", "", "12 45", "١٢٣٤٥"):
        r = anon.post("/api/agent/claim", json={"project": "pair-app", "code": bad})
        check(f"claim با رمز نامعتبر {bad!r} → 400", r.status_code == 400, r.text[:120])
    for weak in ("12345", "00000", "99999", "54321"):
        r = anon.post("/api/agent/claim", json={"project": "pair-app", "code": weak})
        check(f"claim با رمز ضعیف {weak} → 400", r.status_code == 400, r.text[:120])
    r = anon.post("/api/agent/claim", json={"project": "no-such-app", "code": "81234"})
    check("پروژهٔ ناموجود → 404", r.status_code == 404)
    r = anon.post("/api/agent/claim", json={"project": "NOPE!!", "code": "81234"})
    check("slug نامعتبر → 404", r.status_code == 404)

    print("— جریان اصلی —")
    CODE = "71409"
    r = anon.post("/api/agent/claim", json={"project": "pair-app", "code": CODE})
    check("claim معتبر → 200 با claim_id/secret",
          r.status_code == 200 and r.json().get("claim_id", "").startswith("apc_")
          and r.json().get("claim_secret", "").startswith("apcsk_"), r.text[:200])
    CID = r.json().get("claim_id", "")
    SEC = r.json().get("claim_secret", "")
    check("پنجره‌ها: تأیید ۱۲۰ث، برداشت ۱۸۰۰ث",
          r.json().get("approve_window_s") == 120 and r.json().get("pickup_window_s") == 1800)
    disk = Path(os.environ["DATA_DIR"]) / "dev" / "agent-pairing.json"
    txt = disk.read_text(encoding="utf-8")
    check("روی دیسک فقط هش است", CODE not in txt and SEC not in txt and "apcsk_" not in txt)

    r = anon.post("/api/agent/claim/poll",
                  json={"claim_id": CID, "claim_secret": "apcsk_" + "f" * 48})
    check("poll با راز غلط → unknown", r.status_code == 200 and r.json().get("status") == "unknown")
    r = anon.post("/api/agent/claim/poll", json={"claim_id": CID, "claim_secret": SEC})
    check("poll پیش از تأیید → pending", r.status_code == 200 and r.json().get("status") == "pending")

    print("— تأیید انسانی: CSRF و دانه‌بندی پروژه —")
    r = cu.post("/projects/pair-app/agent-approve", data={"code": CODE, "csrf": other_csrf},
                follow_redirects=False)
    check("کاربر دیگر نمی‌تواند پروژهٔ میزبان را تأیید کند → ریدایرکت not_found",
          r.status_code == 303 and "/projects?m=not_found" in r.headers.get("location", ""))
    r = ca.post("/projects/pair-app/agent-approve", data={"code": CODE},
                follow_redirects=False)
    check("فرم تأیید بدون CSRF → رد", r.status_code in (303, 403)
          and (r.status_code != 303 or "m=agent_approve_failed" in r.headers.get("location", "")
               or "csrf" in r.headers.get("location", "")))
    r = ca.post("/projects/pair-app/agent-approve", data={"code": "55555", "csrf": admin_csrf},
                follow_redirects=False)
    check("تأیید رمز غلط → ریدایرکت failed",
          r.status_code == 303 and "m=agent_approve_failed" in r.headers.get("location", ""))
    r = ca.post("/projects/pair-app/agent-approve", data={"code": CODE, "csrf": admin_csrf},
                follow_redirects=False)
    check("تأیید رمز درست → ریدایرکت ok",
          r.status_code == 303 and "m=agent_approved" in r.headers.get("location", ""))

    r = anon.post("/api/agent/claim/poll", json={"claim_id": CID, "claim_secret": SEC})
    body = r.json()
    check("poll پس از تأیید → approved + pst_",
          r.status_code == 200 and body.get("status") == "approved"
          and body.get("agent_token", "").startswith("pst_"), r.text[:200])
    TOK = body.get("agent_token", "")
    r = anon.post("/api/agent/claim/poll", json={"claim_id": CID, "claim_secret": SEC})
    check("برداشت دوم → unknown (یک‌بارمصرف)",
          r.status_code == 200 and r.json().get("status") == "unknown")

    print("— توکن جفت‌شده واقعاً کار می‌کند و دانه‌اش همان پروژه است —")
    r = anon.get("/api/bridge/status", headers={"Authorization": f"Bearer {TOK}"})
    check("bridge status با توکن جفت‌شده → 200",
          r.status_code == 200 and r.json().get("project", {}).get("slug") == "pair-app",
          r.text[:150])

    print("— audit: رویدادها هست، مقدار رمز/راز هرگز —")
    audit_txt = (Path(os.environ["DATA_DIR"]) / "dev" / "agentpair-audit.jsonl").read_text(
        encoding="utf-8")
    for ev in ("\"claim\"", "approved", "token_issued", "claim_refused", "approve_failed"):
        check(f"audit: {ev} ثبت شده", ev in audit_txt)
    check("audit: مقدار رمز هرگز لاگ نشده", CODE not in audit_txt and "71409" not in audit_txt)
    check("audit: راز ادعا هرگز لاگ نشده", SEC not in audit_txt)

    print("— انقضا و سقف‌ها (فراخوانی مستقیم سرویس) —")
    old_w = apair.APPROVE_WINDOW_S
    apair.APPROVE_WINDOW_S = 1
    rc = apair.mint_claim(proj.id, "30411", ip="10.1.1.1")
    apair.APPROVE_WINDOW_S = old_w
    time.sleep(1.3)
    ra = apair.approve(proj.id, "30411")
    check("انقضای پنجرهٔ تأیید → no_pending_claim", ra.get("error") == "no_pending_claim")
    rp = apair.collect(SessionLocal(), rc.get("claim_id", ""), rc.get("claim_secret", ""))
    check("poll پس از انقضای تأیید → expired", rp.get("status") == "expired")

    rc2 = apair.mint_claim(proj.id, "30412", ip="10.1.1.2")
    apair.approve(proj.id, "30412")
    old_p = apair.PICKUP_WINDOW_S
    apair.PICKUP_WINDOW_S = -1
    try:
        rp2 = apair.collect(SessionLocal(), rc2.get("claim_id", ""), rc2.get("claim_secret", ""))
    finally:
        apair.PICKUP_WINDOW_S = old_p
    check("انقضای پنجرهٔ برداشت → unknown (جاروب شده)", rp2.get("status") == "unknown")

    # دانه‌بندی پروژه در سطح سرویس: تأیید پروژهٔ دیگر، ادعای پروژهٔ اول را باز نمی‌کند
    rc3 = apair.mint_claim(proj.id, "30413", ip="10.1.1.3")
    wrong = apair.approve(other.id, "30413")
    check("تأیید با پروژهٔ اشتباه → no_pending_claim (دانه‌بندی)",
          wrong.get("error") == "no_pending_claim")
    rr = apair.approve(proj.id, "30413")
    check("تأیید با پروژهٔ درست → ok", rr.get("ok") is True)

    # سقف ادعای هم‌زمان هر IP = 2
    apair.mint_claim(proj.id, "40111", ip="10.2.2.2")
    apair.mint_claim(proj.id, "40112", ip="10.2.2.2")
    r3 = apair.mint_claim(proj.id, "40113", ip="10.2.2.2")
    check("سقف ادعای هم‌زمان هر IP = 2 → سومی busy",
          r3.get("error") == "busy" and r3.get("status") == 429)

    # نرخ: ۵ در ۵ دقیقه
    old_rw, old_rm = apair.RATE_WINDOW_S, apair.RATE_MAX
    apair.RATE_WINDOW_S, apair.RATE_MAX = 1, 2
    try:
        apair.mint_claim(other.id, "50211", ip="10.3.3.3")
        apair.mint_claim(other.id, "50212", ip="10.3.3.3")
        rr2 = apair.mint_claim(other.id, "50213", ip="10.3.3.3")
        check("نرخ‌سنجی → rate_limited",
              rr2.get("error") == "rate_limited" and rr2.get("status") == 429)
    finally:
        apair.RATE_WINDOW_S, apair.RATE_MAX = old_rw, old_rm

    # جاروی دیکشنری نرخ
    apair._RATE.clear()
    for i in range(600):
        apair._RATE[f"10.9.9.{i}"] = [0.0]
    rr3 = apair.mint_claim(other.id, "60217", ip="10.4.4.4")
    check("جاروی نرخ: کهنه‌ها حذف و ادعای تازه پذیرفته شد",
          rr3.get("ok") and len(apair._RATE) < 100)

print(f"\n# جمع‌بندی: {PASS} سبز، {FAIL} قرمز")
sys.exit(1 if FAIL else 0)
