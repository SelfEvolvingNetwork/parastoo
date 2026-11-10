"""تست دودی تیکتینگ عضو‌به‌عضو (v0.5.17) — چرخهٔ کامل HTTP روی /api/tickets*.

پوشش: احراز دومسیر (نشست/توکن پل)، CSRF انسان، ماتریس مسیریابی، اسکیمای
سخت، توان‌مندی/تکرار نزدیک، ماشین حالت تا escalated، رنداکتی تیکت حساس،
IDOR-safe و سقف تیکت باز هر جفت. بدون داکر/گیتا (حالت pending کافی است).

چهار فراخوانندهٔ جدا (کوکی‌ها قاطی نشوند):
  ca = نشست ادمین (میزبان) | cc = نشست کلاینت | ag = فقط توکن پل | anon = بی‌احراز
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

os.environ.update(
    DATABASE_URL="sqlite:////tmp/parastoo-tickets-test.db",
    PARASTOO_DISABLE_WORKERS="1",
    SECRET_KEY="test-secret-key",
    BASE_DOMAIN="127.0.0.1.nip.io",
    TLS_ENABLED="false",
    REGISTER_OPEN="true",
    DATA_DIR="/tmp/parastoo-smoke-audit",
)
if os.path.exists("/tmp/parastoo-tickets-test.db"):
    os.remove("/tmp/parastoo-tickets-test.db")

sys.path.insert(0, str(ROOT / "core"))

from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project  # noqa: E402
from app.services import bridge as bsvc  # noqa: E402

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


def mk_payload(to, title, **kw):
    p = {"to": to, "kind": kw.pop("kind", "bug"), "severity": kw.pop("severity", "high"),
         "title": title, "observed": kw.pop("observed", "پاسخ ۵۰۰ دیدم"),
         "expected": kw.pop("expected", "پاسخ ۲۰۰ باشد")}
    p.update(kw)
    return p


AC = lambda u, tok: {"X-CSRF-Token": tok}   # هدر CSRF انسان


with TestClient(app, follow_redirects=False) as ca, \
        TestClient(app, follow_redirects=False) as cc, \
        TestClient(app, follow_redirects=False) as ag, \
        TestClient(app, follow_redirects=False) as anon:
    print("— راه‌اندازی: ادمین (میزبان)، کلاینت، پروژه، توکن پل —")
    r = ca.post("/setup", data={"email": "admin@test.ir", "password": "secret1",
                                "name": "میزبان"}, follow_redirects=False)
    check("ادمین ساخته شد (ورود خودکار)", r.status_code == 303)
    admin_csrf = security.csrf_token(1)

    r = ca.post("/projects/new", data={"name": "اپ تست", "slug": "ticket-app",
                                       "description": "", "csrf": admin_csrf},
                follow_redirects=False)
    check("پروژه ساخته شد", r.status_code == 303)

    r = cc.post("/register", data={"email": "client@test.ir", "password": "secret1",
                                   "name": "کلاینت"}, follow_redirects=False)
    check("کلاینت ثبت‌نام شد (ورود خودکار)", r.status_code == 303)
    client_csrf = security.csrf_token(2)

    db = SessionLocal()
    proj = db.query(Project).filter(Project.slug == "ticket-app").first()
    raw_token, _row = bsvc.mint(db, proj, "smoke-agent")
    db.close()
    AH = {"Authorization": f"Bearer {raw_token}"}

    print("— احراز هویت و CSRF —")
    r = anon.get("/api/tickets")
    check("بدون احراز → 401", r.status_code == 401)
    r = ca.get("/api/tickets")
    check("انسان با نشست → 200", r.status_code == 200 and r.json().get("ok") is True)
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "بدون csrf"))
    check("POST انسان بدون CSRF → 403", r.status_code == 403)

    print("— ماتریس مسیریابی —")
    r = cc.post("/api/tickets", json=mk_payload("project:ticket-app", "کلاینت بی‌رابطه"),
                headers=AC(2, client_csrf))
    check("کلاینت→پروژهٔ بی‌رابطه → 403 routing", r.status_code == 403
          and r.json().get("code") == "routing", r.text[:150])
    r = cc.post("/api/tickets", json=mk_payload("user:999", "مقصد ناموجود"),
                headers=AC(2, client_csrf))
    check("مقصد ناموجود → 400", r.status_code == 400)
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "تیکت میزبان به پروژه"),
                headers=AC(1, admin_csrf))
    check("میزبان→پروژه → 200", r.status_code == 200
          and r.json().get("duplicate") is False, r.text[:150])
    tid = r.json()["ticket"]["id"]

    print("— اسکیمای سخت —")
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "تیکت با kind بد",
                                                kind="چیز دیگر"),
                headers=AC(1, admin_csrf))
    check("kind نامعتبر → 400 schema", r.status_code == 400)
    r = ca.post("/api/tickets", json={"to": "project:ticket-app", "kind": "bug",
                                      "severity": "high", "title": "بدون مشاهده",
                                      "expected": "x"},
                headers=AC(1, admin_csrf))
    check("observed الزامی → 400", r.status_code == 400)
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "پیشنهاد برچسب فارسی",
                                                kind="پیشنهاد", severity="بحرانی"),
                headers=AC(1, admin_csrf))
    check("برچسب فارسی نرمال می‌شود", r.status_code == 200
          and r.json()["ticket"]["kind"] == "suggestion"
          and r.json()["ticket"]["kind_fa"] == "پیشنهاد")

    print("— ضد-طوفان —")
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "تیکت میزبان به پروژه"),
                headers=AC(1, admin_csrf))
    check("ارسال دوبارهٔ همان → همان تیکت (duplicate)", r.status_code == 200
          and r.json().get("duplicate") is True and r.json()["ticket"]["id"] == tid)
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "تیکت  میزبان  به پروژه ",
                                                observed="کمی متفاوت"),
                headers=AC(1, admin_csrf))
    check("تکرار نزدیک → 409 با duplicate_of", r.status_code == 409
          and r.json().get("duplicate_of") == tid, r.text[:150])
    # سقف تیکت باز هر جفت (۵): دو باز از قبل + سه مستقل → جمعاً ۵
    for i in range(3):
        rr = ca.post("/api/tickets", json=mk_payload(
            "project:ticket-app", f"تیکت مستقل شمارهٔ {i} که {i*7} فرق دارد",
            kind="suggestion"), headers=AC(1, admin_csrf))
        if rr.status_code != 200:
            check(f"تیکت مستقل {i+1}", False, rr.text[:120])
    r = ca.post("/api/tickets", json=mk_payload("project:ticket-app", "تیکت ششم مستقل ۹۹"),
                headers=AC(1, admin_csrf))
    check("ششمین تیکت باز جفت → 429", r.status_code == 429, r.text[:120])

    print("— ماشین حالت (عامل = گیرنده) —")
    r = ca.get(f"/api/tickets/{tid}")
    check("انسان جزئیات را می‌بیند", r.status_code == 200
          and r.json()["ticket"]["observed"] == "پاسخ ۵۰۰ دیدم")
    r = anon.post(f"/api/tickets/{tid}/status", json={"status": "ack"})
    check("عاملِ بی‌توکن → 401", r.status_code == 401)
    r = ca.post(f"/api/tickets/{tid}/status", json={"status": "accepted"},
                headers=AC(1, admin_csrf))
    check("open→accepted مستقیم ممنوع → 409", r.status_code == 409, r.text[:120])
    r = ca.post(f"/api/tickets/{tid}/status", json={"status": "ack"},
                headers=AC(1, admin_csrf))
    check("open→ack توسط فرستنده ممنوع → 409", r.status_code == 409)
    r = ag.get(f"/api/tickets/{tid}", headers=AH)
    check("عامل جزئیات را می‌بیند", r.status_code == 200
          and r.json()["ticket"]["status"] == "open")
    r = ag.post(f"/api/tickets/{tid}/status", json={"status": "ack"}, headers=AH)
    check("open→ack توسط گیرنده", r.status_code == 200 and r.json()["status"] == "ack")
    r = ag.post(f"/api/tickets/{tid}/status", json={"status": "accepted"}, headers=AH)
    check("ack→accepted", r.status_code == 200)
    r = ag.post(f"/api/tickets/{tid}/status",
                json={"status": "resolved", "evidence": "بدون سند"}, headers=AH)
    check("resolved بدون سند → 409", r.status_code == 409)
    r = ag.post(f"/api/tickets/{tid}/status",
                json={"status": "resolved", "evidence": "رفع شد در کامیت a1b2c3d4"},
                headers=AH)
    check("resolved با کامیت → 200", r.status_code == 200
          and r.json()["status"] == "resolved")
    r = ca.post(f"/api/tickets/{tid}/status", json={"status": "open"},
                headers=AC(1, admin_csrf))
    check("reopen فرستنده (اول)", r.status_code == 200 and r.json()["status"] == "open")
    ag.post(f"/api/tickets/{tid}/status", json={"status": "ack"}, headers=AH)
    ag.post(f"/api/tickets/{tid}/status", json={"status": "accepted"}, headers=AH)
    ag.post(f"/api/tickets/{tid}/status",
            json={"status": "resolved", "evidence": "fix مجدد b2c3d4e5"}, headers=AH)
    r = ca.post(f"/api/tickets/{tid}/status", json={"status": "open"},
                headers=AC(1, admin_csrf))
    check("reopen دوم → escalated برای انسان", r.status_code == 200
          and r.json()["status"] == "escalated")
    r = ca.post(f"/api/tickets/{tid}/status", json={"status": "closed"},
                headers=AC(1, admin_csrf))
    check("ادمین بن‌بست را می‌بندد", r.status_code == 200
          and r.json()["status"] == "closed")
    r = ag.post(f"/api/tickets/{tid}/comments", json={"body": "بسته شد"}, headers=AH)
    check("کامنت روی بسته‌شده ممنوع", r.status_code == 409)

    print("— تیکت حساس: تیکت داده است نه فرمان —")
    r = ca.post("/api/tickets", json=mk_payload(
        "project:ticket-app", "تیکت حساس ۳۱",
        observed="پاسخ شامل pst_AbCdEf123456789 لو رفت"),
        headers=AC(1, admin_csrf))
    check("تیکت حساس ساخته شد (چهار باز مانده بود)", r.status_code == 200, r.text[:120])
    sid = r.json()["ticket"]["id"]
    check("is_sensitive ثبت شد", r.json()["ticket"]["is_sensitive"] is True)
    r = ag.get(f"/api/tickets/{sid}", headers=AH)
    check("عامل محتوای رنداکتی می‌بیند", "[محتوای محرمانه" in r.json()["ticket"]["observed"]
          and r.json()["ticket"]["needs_human"] is True)
    r = ca.get(f"/api/tickets/{sid}")
    check("انسان متن کامل می‌بیند", "pst_" in r.json()["ticket"]["observed"])
    r = ag.post(f"/api/tickets/{sid}/comments", json={"body": "پاسخ عامل"}, headers=AH)
    check("عامل روی تیکت حساس نمی‌نویسد → 403", r.status_code == 403)
    r = ca.post(f"/api/tickets/{sid}/comments", json={"body": "بررسی انسانی شد"},
                headers=AC(1, admin_csrf))
    check("انسان می‌نویسد", r.status_code == 200)

    print("— IDOR-safe و صندوق‌ها —")
    r = cc.get(f"/api/tickets/{tid}")
    check("غریبه → 404 (نه 403)", r.status_code == 404)
    r = cc.get("/api/tickets?box=inbox")
    check("صندوق کلاینتِ غریبه خالی", r.status_code == 200
          and r.json()["tickets"] == [])
    r = ca.get("/api/tickets?box=sent")
    check("صندوق ارسالی میزبان", r.status_code == 200
          and all(t["sender"] == "user:1" for t in r.json()["tickets"]))
    r = ag.get("/api/tickets?box=weird", headers=AH)
    check("box نامعتبر → 400", r.status_code == 400)

    print("— دفتر رخدادها —")
    r = ag.get(f"/api/tickets/{tid}", headers=AH)
    kinds = [e["kind"] for e in r.json()["events"]]
    check("توالی رخدادها ثبت شده", "create" in kinds and "status" in kinds
          and "escalate" in kinds)

    print("— قلاب اطلاع‌رسانی در status پل (Cycle 2) —")
    r = ag.get("/api/bridge/status", headers=AH)
    tk = r.json().get("tickets")
    check("قلاب tickets در status هست", isinstance(tk, dict)
          and set(tk) == {"inbox_open", "sent_open"}, r.text[:150])
    # tid بسته شد؛ باز‌ها: فارسی + ۳ مستقل + حساس = ۵ — همگی به پروژه
    check("صندوق باز پروژه = ۵", tk and tk["inbox_open"] == 5
          and tk["sent_open"] == 0, str(tk))

    print("— audit اختصاصی تیکت (فقط فراخوانی عامل) —")
    r = ag.post("/api/tickets", headers=AH,
                json=mk_payload("user:1", "تیکت از عامل پروژه به میزبان"))
    check("عامل به میزبان تیکت می‌زند", r.status_code == 200, r.text[:120])
    agent_tid = r.json()["ticket"]["id"]
    r = ag.post("/api/tickets", headers=AH,
                json={"to": "user:1", "kind": "bug", "severity": "high",
                      "title": "بدون مشاهده از عامل", "expected": "x"})
    check("رد اسکیمای عامل → 400", r.status_code == 400)
    r = ag.post(f"/api/tickets/{agent_tid}/comments",
                json={"body": "عامل پیگیری می‌کند"}, headers=AH)
    check("کامنت عامل روی تیکت خودش", r.status_code == 200, r.text[:120])
    r = ag.get("/api/bridge/audit?limit=100", headers=AH)
    records = r.json().get("records", [])
    actions = {rec.get("action") for rec in records}
    check("audit اختصاصی — عمل‌های متمایز",
          {"tickets.create", "tickets.detail", "tickets.status",
           "tickets.comment", "tickets.list"} <= actions, str(sorted(actions)))
    check("audit رد schema هم ثبت می‌شود", any(
        rec.get("action") == "tickets.create" and rec.get("ok") is False
        for rec in records))
    r = ag.get("/api/bridge/status", headers=AH)
    tk = r.json().get("tickets")
    check("صندوق ارسالی پروژه حالا ۱", tk and tk["sent_open"] == 1, str(tk))

    print("— CLI عامل (pst.py) — آفلاین با call قلابی —")
    import importlib.util
    import io
    from contextlib import redirect_stdout
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location(
        "pst_cli", ROOT / "core" / "app" / "web" / "static" / "pst.py")
    pst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pst)

    seen = []

    def fake_call(method, path, body=None, conf=None, timeout=180):
        seen.append((method, path, body))
        if path.startswith("/api/tickets?"):
            return 200, {"ok": True, "tickets": [{
                "id": 7, "status": "open", "severity": "high",
                "kind_fa": "باگ", "title": "تیکت قلابی برای CLI",
                "sender": "user:1", "receiver": "project:ticket-app",
                "created_at": "2026-11-10 10:00:00", "is_sensitive": False}]}
        if path == "/api/tickets/7":
            return 200, {"ok": True, "ticket": {
                "id": 7, "status": "open", "severity": "high", "kind_fa": "باگ",
                "title": "تیکت قلابی برای CLI", "sender": "user:1",
                "receiver": "project:ticket-app", "observed": "خطای ۵۰۰",
                "expected": "خطای ۲۰۰", "body": "", "evidence": [],
                "is_sensitive": True, "needs_human": True,
                "created_at": "2026-11-10 10:00:00"},
                "events": [{"id": 1, "kind": "create", "actor": "user:1",
                            "data": {}, "created_at": "2026-11-10 10:00:00"}]}
        if path == "/api/tickets":
            return 200, {"ok": True, "duplicate": False,
                         "ticket": {"id": 9, "status": "open"}}
        if path.endswith("/comments"):
            return 200, {"ok": True, "event_id": 5, "status": "ack"}
        if path.endswith("/status"):
            return 200, {"ok": True, "status": "resolved", "event_id": 6}
        return 404, {"ok": False, "error": "مسیر قلابی ناشناخته"}

    pst.call = fake_call
    buf = io.StringIO()
    with redirect_stdout(buf):
        pst.cmd_tickets(SimpleNamespace(box="inbox", limit=50))
    out = buf.getvalue()
    check("pst tickets فهرست را نشان می‌دهد", "#7" in out and "باگ" in out
          and "user:1 → project:ticket-app" in out, out[:120])
    check("درخواست به مسیر درست رفت", seen[-1][1] == "/api/tickets?box=inbox&limit=50")

    buf = io.StringIO()
    with redirect_stdout(buf):
        pst.cmd_ticket(SimpleNamespace(id=7))
    out = buf.getvalue()
    check("pst ticket حساس هشدار انسانی می‌دهد",
          "تیکت حساس" in out and "رنداکتی" in out, out[:150])
    check("pst ticket رخدادها را نشان می‌دهد", "create" in out)

    buf = io.StringIO()
    with redirect_stdout(buf):
        pst.cmd_ticket_new(SimpleNamespace(
            to="user:1", kind="bug", severity="high", title="عنوان عامل",
            observed="مشاهدهٔ عامل", expected="انتظار عامل",
            body="شرح", evidence=["https://x/y", "commit:abcd123"]))
    out = buf.getvalue()
    m, p_, b = seen[-1]
    check("pst ticket-new بدنهٔ کامل می‌فرستد",
          m == "POST" and p_ == "/api/tickets" and b["evidence"] == ["https://x/y", "commit:abcd123"]
          and b["to"] == "user:1")
    check("pst ticket-new شناسه را برمی‌گرداند", "#9" in out)

    buf = io.StringIO()
    with redirect_stdout(buf):
        pst.cmd_ticket_comment(SimpleNamespace(id=7, body="پیگیری عامل"))
    check("pst ticket-comment وضعیت را برمی‌گرداند", "ack" in buf.getvalue())
    m, p_, b = seen[-1]
    check("مسیر کامنت درست", m == "POST" and p_ == "/api/tickets/7/comments"
          and b == {"body": "پیگیری عامل"})

    buf = io.StringIO()
    with redirect_stdout(buf):
        pst.cmd_ticket_status(SimpleNamespace(id=7, status="resolved",
                                              evidence="fix در abcd123"))
    check("pst ticket-status گذار را نشان می‌دهد", "resolved" in buf.getvalue())
    m, p_, b = seen[-1]
    check("سندِ رفع همراه گذار می‌رود",
          b == {"status": "resolved", "evidence": "fix در abcd123"})

    ap = pst.build_parser()
    ns = ap.parse_args(["tickets", "--box", "inbox", "--limit", "5"])
    check("آرگ‌پارس پنج فرمان تیکت را ثبت کرده",
          ns.fn is pst.cmd_tickets and ns.box == "inbox" and ns.limit == 5)
    ns = ap.parse_args(["ticket-status", "3", "ack"])
    check("ticket-status بدون evidence هم پارس می‌شود",
          ns.fn is pst.cmd_ticket_status and ns.id == 3 and ns.status == "ack"
          and ns.evidence is None)

    print("— UI پنل (Cycle 3) — صندوق، جزئیات، فرم، گذار نقش‌محور —")
    r = ca.get("/tickets")
    check("صفحهٔ صندوق برای انسان رندر شد", r.status_code == 200
          and "صندوق ورودی" in r.text and "تیکت جدید" in r.text, r.status_code)
    check("پیوند ناوبری تیکت با نشان باز در صفحه هست",
          ">تیکت‌ها" in r.text and "badge-warn" in r.text)
    r = anon.get("/tickets")
    check("بی‌احراز → ورود (303)", r.status_code == 303
          and "/login" in r.headers.get("location", ""))
    r = cc.get("/dashboard")
    check("کارت «تیکت باز در صندوق» در داشبورد", "تیکت باز در صندوق" in r.text)
    # تیکت میزبان→کلاینت (گیرندهٔ انسانی) برای آزمون گذار از فرم پنل
    r = ca.post("/api/tickets", json=mk_payload("user:2", "تیکت میزبان به کلاینت برای UI"),
                headers=AC(1, admin_csrf))
    check("میزبان→کلاینت ساخته شد", r.status_code == 200, r.text[:120])
    ui_tid = r.json()["ticket"]["id"]
    r = cc.get(f"/tickets/{ui_tid}")
    check("جزئیات برای گیرندهٔ انسانی رندر شد", r.status_code == 200
          and "دریافت شد" in r.text, r.status_code)
    r = cc.post(f"/tickets/{ui_tid}/status", data={"status": "ack", "csrf": client_csrf},
                follow_redirects=False)
    check("گذار ack از فرم پنل", r.status_code == 303
          and f"/tickets/{ui_tid}?m=ticket_status_changed" in r.headers.get("location", ""),
          r.headers.get("location", ""))
    r = cc.get(f"/tickets/{ui_tid}")
    check("وضعیت در جزئیات به‌روز شد", "دریافت‌شده" in r.text)
    r = cc.post(f"/tickets/{ui_tid}/comment",
                data={"body": "سلام؛ در بررسی است", "csrf": client_csrf},
                follow_redirects=False)
    check("کامنت از فرم پنل", r.status_code == 303
          and "m=ticket_commented" in r.headers.get("location", ""))
    r = cc.get(f"/tickets/{ui_tid}")
    check("کامنت در دفتر رخدادها دیده می‌شود", "در بررسی است" in r.text)
    r = cc.get(f"/tickets/{tid}")
    check("غریبه در UI → بدون لو دادن وجود به صندوق برمی‌گردد",
          r.status_code == 303 and "m=not_found" in r.headers.get("location", ""))
    r = cc.post("/tickets/new", data={"to": "user:1", "kind": "bug", "severity": "بالا",
                                      "title": "کلاینت به میزبان از فرم", "observed": "مشاهده",
                                      "expected": "انتظار", "csrf": client_csrf},
                follow_redirects=False)
    check("فرم کلاینت→کاربر با مسیریابی رد شد", r.status_code == 200
          and "مجاز نیست" in r.text and "کلاینت به میزبان از فرم" in r.text,
          r.status_code)
    r = ca.post("/tickets/new", data={"to": "user:2", "kind": "پیشنهاد", "severity": "کم",
                                      "title": "تیکت از فرم پنل میزبان",
                                      "observed": "مشاهدهٔ فرم پنل", "expected": "انتظار فرم پنل",
                                      "csrf": admin_csrf},
                follow_redirects=False)
    check("فرم ساخت میزبان→کلاینت", r.status_code == 303
          and "/tickets/" in r.headers.get("location", ""), r.headers.get("location", ""))
    r = cc.post("/tickets/new", data={"to": "user:1", "kind": "bug", "title": "بدون csrf",
                                      "observed": "x", "expected": "y"},
                follow_redirects=False)
    check("فرم بدون CSRF → m=csrf", r.status_code == 303
          and "m=csrf" in r.headers.get("location", ""))
    r = ca.get("/api/tickets/summary")
    check("خلاصهٔ صندوق (API/ادمین)", r.status_code == 200
          and r.json().get("ok") is True and r.json().get("sent_open") >= 1, r.text[:120])
    r = cc.get("/api/tickets/summary")
    check("خلاصهٔ صندوق برای کلاینت", r.status_code == 200
          and r.json().get("inbox_open") >= 1)

print(f"\nنتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
