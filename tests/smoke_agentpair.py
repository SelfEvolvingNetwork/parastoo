"""تست دودی جفت‌سازی عامل پروژه (v0.5.28) — چرخهٔ کامل HTTP روی /api/agent/*.

پوشش (آینهٔ smoke_pairing پل ادمین v0.8، با دانهٔ پروژه و رمز سرورساخته):
  عامل:  GET /api/agent/onboarding?project=<slug>&iid=<شناسهٔ دعوت>
         ← رمز ۵ رقمی یک‌بارمصرفِ «سرورساخته» + claim_id + claim_secret
  کاربر: POST /projects/<slug>/agent-approve (فرم پنل + CSRF)
  عامل:  POST /api/agent/claim/poll ← توکن pst_ ← GET /api/bridge/status

قرمزها: iid غلط/مصرف‌شده، بازنشستگی مسیر عامل‌ساخته (410)، پروژهٔ ناموجود،
CSRF فرم تأیید، دانه‌بندی پروژه در تأیید (کاربر دیگر)، برداشت دوباره،
رمز/راز غلط، انقضای پنجره‌ها، سقف‌ها، فقط-هش روی دیسک، audit بدون مقدار
رمز/iid، متن دعوت بی‌رمز، یک دعوت = یک عامل.
"""
import json
import os
import re
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


def get_code(slug, iid):
    """شبیه‌سازی عامل: GET آدرس دعوت → رمز سرورساخته + اعتبارنامه‌ها."""
    return anon.get(f"/api/agent/onboarding?project={slug}&iid={iid}")


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

    print("— صفحهٔ پروژه: متن دعوت بی‌رمز با شناسهٔ دعوت —")
    r = ca.get("/projects/pair-app")
    check("صفحهٔ پروژه 200", r.status_code == 200)
    html = r.text
    check("کارت دعوت بدون توکن هست", "agent-invite-text" in html and "دعوت بدون توکن" in html)
    check("متن دعوت: slug و آدرس onboarding دارد",
          "pair-app" in html and "/api/agent/onboarding?project=pair-app" in html
          and ("&amp;iid=" in html or "&iid=" in html))
    m = re.search(r"iid=(pinv_[0-9a-f]{24})", html)
    check("متن دعوت: شناسهٔ دعوت pinv_ با ۲۴ هگز است", bool(m))
    IID = m.group(1) if m else ""
    check("متن دعوت: هیچ pst_ خام در آن نیست",
          "PST_TOKEN" not in html.split("agent-invite-text")[1].split("</pre>")[0])
    check("فرم تأیید عامل هست", "/projects/pair-app/agent-approve" in html)

    print("— رندر دوباره: دعوت تازه، دعوت قبلی همان پروژه می‌میرد —")
    iid1_disk = Path(os.environ["DATA_DIR"]) / "dev" / "agent-invites.json"
    before = iid1_disk.read_text(encoding="utf-8")
    check("روی دیسک فقط هش شناسهٔ دعوت است", IID not in before)
    r = ca.get("/projects/pair-app")
    m2 = re.search(r"iid=(pinv_[0-9a-f]{24})", r.text)
    IID2 = m2.group(1) if m2 else ""
    check("رندر دوباره → دعوت تازه", IID2 and IID2 != IID)
    r = get_code("pair-app", IID)
    check("دعوت قبلی پس از رندر تازه → 404", r.status_code == 404)

    print("— سند آشناسازی: بدون پارامتر فقط دستورالعمل —")
    r = anon.get("/api/agent/onboarding")
    check("onboarding بدون پارامتر → 200 invite_required",
          r.status_code == 200 and r.json().get("invite_required") is True
          and "pairing" not in r.json())
    check("onboarding: پروتکل 0.8 و راهنما دارد",
          r.json().get("protocol") == "onboarding/0.8" and "/docs/agent" in r.json().get("guide", ""))
    r = get_code("pair-app", "pinv_" + "f" * 24)
    check("iid غلط → 404", r.status_code == 404)
    r = get_code("no-such-app", IID2)
    check("پروژهٔ ناموجود → 404", r.status_code == 404)
    r = anon.get("/api/agent/onboarding?project=NOPE!!&iid=" + IID2)
    check("slug نامعتبر → 404", r.status_code == 404)

    print("— مسیر عامل‌ساخته بازنشسته → 410 —")
    r = anon.post("/api/agent/claim", json={"project": "pair-app", "code": "71409"})
    check("POST /api/agent/claim (عامل‌ساخته) → 410", r.status_code == 410)
    r = anon.post("/api/agent/claim", json={"project": "pair-app", "code": "12345"})
    check("claim عامل‌ساخته با رمز ضعیف هم → 410", r.status_code == 410)

    print("— جریان اصلی: رمز سرورساخته —")
    r = get_code("pair-app", IID2)
    check("onboarding با iid معتبر → 200 + pairing",
          r.status_code == 200 and "pairing" in r.json(), r.text[:200])
    body = r.json().get("pairing", {})
    CODE = str(body.get("code", ""))
    check("رمز سرورساخته دقیقاً ۵ رقم ASCII است",
          len(CODE) == 5 and CODE.isascii() and CODE.isdigit())
    check("دستور چاپ بدون محاسبه هست", "چاپ" in str(body.get("print_for_user", "")))
    CID = body.get("claim_id", "")
    SEC = body.get("claim_secret", "")
    check("claim_id/claim_secret در پاسخ است",
          CID.startswith("apc_") and SEC.startswith("apcsk_"))
    check("پنجره‌ها: تأیید ۹۰۰ث، برداشت ۱۸۰۰ث",
          body.get("approve_window_s") == 900 and body.get("pickup_window_s") == 1800)
    disk = Path(os.environ["DATA_DIR"]) / "dev" / "agent-pairing.json"
    txt = disk.read_text(encoding="utf-8")
    check("روی دیسک فقط هش است (رمز/راز/iid)",
          CODE not in txt and SEC not in txt and IID2 not in txt and "apcsk_" not in txt)

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

    print("— یک دعوت = یک عامل —")
    r = get_code("pair-app", IID2)
    check("GET با iid مصرف‌شده → 404", r.status_code == 404)

    r = anon.post("/api/agent/claim/poll", json={"claim_id": CID, "claim_secret": SEC})
    body2 = r.json()
    check("poll پس از تأیید → approved + pst_",
          r.status_code == 200 and body2.get("status") == "approved"
          and body2.get("agent_token", "").startswith("pst_"), r.text[:200])
    TOK = body2.get("agent_token", "")
    r = anon.post("/api/agent/claim/poll", json={"claim_id": CID, "claim_secret": SEC})
    check("برداشت دوم → unknown (یک‌بارمصرف)",
          r.status_code == 200 and r.json().get("status") == "unknown")

    print("— توکن جفت‌شده واقعاً کار می‌کند و دانه‌اش همان پروژه است —")
    r = anon.get("/api/bridge/status", headers={"Authorization": f"Bearer {TOK}"})
    check("bridge status با توکن جفت‌شده → 200",
          r.status_code == 200 and r.json().get("project", {}).get("slug") == "pair-app",
          r.text[:150])

    print("— audit: رویدادها هست، مقدار رمز/راز/iid هرگز —")
    audit_txt = (Path(os.environ["DATA_DIR"]) / "dev" / "agentpair-audit.jsonl").read_text(
        encoding="utf-8")
    for ev in ("onboarding_code", "onboarding_refused", "approved", "token_issued",
               "claim_retired", "approve_failed"):
        check(f"audit: {ev} ثبت شده", ev in audit_txt)
    check("audit: مقدار رمز هرگز لاگ نشده", CODE not in audit_txt)
    check("audit: راز ادعا هرگز لاگ نشده", SEC not in audit_txt)
    check("audit: مقدار iid هرگز لاگ نشده", IID not in audit_txt and IID2 not in audit_txt)

    print("— انقضا و سقف‌ها (فراخوانی مستقیم سرویس) —")
    # انقضای پنجرهٔ تأیید (۹۰۰ ثانیه → ۱ ثانیه در تست)
    iid3, _ = apair.mint_invite(proj.id)
    old_w = apair.APPROVE_WINDOW_S
    apair.APPROVE_WINDOW_S = 1
    rc = apair.mint_server_claim(proj.id, iid3, ip="10.1.1.1")
    apair.APPROVE_WINDOW_S = old_w
    time.sleep(1.3)
    ra = apair.approve(proj.id, rc.get("code", ""))
    check("انقضای پنجرهٔ تأیید → no_pending_claim", ra.get("error") == "no_pending_claim")
    rp = apair.collect(SessionLocal(), rc.get("claim_id", ""), rc.get("claim_secret", ""))
    check("poll پس از انقضای تأیید → expired", rp.get("status") == "expired")

    iid4, _ = apair.mint_invite(proj.id)
    rc2 = apair.mint_server_claim(proj.id, iid4, ip="10.1.1.2")
    apair.approve(proj.id, rc2.get("code", ""))
    old_p = apair.PICKUP_WINDOW_S
    apair.PICKUP_WINDOW_S = -1
    try:
        rp2 = apair.collect(SessionLocal(), rc2.get("claim_id", ""), rc2.get("claim_secret", ""))
    finally:
        apair.PICKUP_WINDOW_S = old_p
    check("انقضای پنجرهٔ برداشت → unknown (جاروب شده)", rp2.get("status") == "unknown")

    # دانه‌بندی پروژه در سطح سرویس: iid پروژهٔ اول روی پروژهٔ دوم کار نمی‌کند
    iid5, _ = apair.mint_invite(proj.id)
    wrong = apair.mint_server_claim(other.id, iid5, ip="10.1.1.4")
    check("iid پروژهٔ دیگر → unknown_invite (دانه‌بندی)",
          wrong.get("error") == "unknown_invite")
    rc3 = apair.mint_server_claim(proj.id, iid5, ip="10.1.1.3")
    wrong2 = apair.approve(other.id, rc3.get("code", ""))
    check("تأیید با پروژهٔ اشتباه → no_pending_claim (دانه‌بندی)",
          wrong2.get("error") == "no_pending_claim")
    rr = apair.approve(proj.id, rc3.get("code", ""))
    check("تأیید با پروژهٔ درست → ok", rr.get("ok") is True)

    # سقف ادعای هم‌زمان هر IP = 2
    iid6, _ = apair.mint_invite(proj.id)
    apair.mint_server_claim(proj.id, iid6, ip="10.2.2.2")
    iid6b, _ = apair.mint_invite(proj.id)
    apair.mint_server_claim(proj.id, iid6b, ip="10.2.2.2")
    iid6c, _ = apair.mint_invite(proj.id)
    r3 = apair.mint_server_claim(proj.id, iid6c, ip="10.2.2.2")
    check("سقف ادعای هم‌زمان هر IP = 2 → سومی busy",
          r3.get("error") == "busy" and r3.get("status") == 429)

    # نرخ: ۵ در ۵ دقیقه
    old_rw, old_rm = apair.RATE_WINDOW_S, apair.RATE_MAX
    apair.RATE_WINDOW_S, apair.RATE_MAX = 1, 2
    try:
        apair.mint_server_claim(other.id, apair.mint_invite(other.id)[0], ip="10.3.3.3")
        apair.mint_server_claim(other.id, apair.mint_invite(other.id)[0], ip="10.3.3.3")
        rr2 = apair.mint_server_claim(other.id, apair.mint_invite(other.id)[0], ip="10.3.3.3")
        check("نرخ‌سنجی → rate_limited",
              rr2.get("error") == "rate_limited" and rr2.get("status") == 429)
    finally:
        apair.RATE_WINDOW_S, apair.RATE_MAX = old_rw, old_rm

    # جاروی دیکشنری نرخ
    apair._RATE.clear()
    for i in range(600):
        apair._RATE[f"10.9.9.{i}"] = [0.0]
    iid7, _ = apair.mint_invite(other.id)
    rr3 = apair.mint_server_claim(other.id, iid7, ip="10.4.4.4")
    check("جاروی نرخ: کهنه‌ها حذف و رمز تازه صادر شد",
          rr3.get("ok") and len(apair._RATE) < 100)

    # انقضای دعوت (TTL)
    iid8, _ = apair.mint_invite(proj.id)
    invites = json.loads(iid1_disk.read_text(encoding="utf-8"))
    for row in invites["invites"]:
        if row.get("iid_sha") == apair._sha(iid8):
            row["expires_ts"] = time.time() - 1
    iid1_disk.write_text(json.dumps(invites), encoding="utf-8")
    rr4 = apair.mint_server_claim(proj.id, iid8, ip="10.6.6.6")
    check("دعوت منقضی → unknown_invite",
          rr4.get("error") == "unknown_invite" and rr4.get("status") == 404)

print(f"\n# جمع‌بندی: {PASS} سبز، {FAIL} قرمز")
sys.exit(1 if FAIL else 0)
