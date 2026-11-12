#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""هارنس کاربر-محور زندهٔ پرستو — لایهٔ «ج» گام M6 سند docs/AI_AGENT_LOOP.fa.md.

برخلاف e2e_live.py (که فقط خواندنی است)، این هارنس پرستو را «مثل کاربر واقعی»
می‌آزماید: ثبت‌نام، ورود، ساخت پروژه، شارژ کیف پول با درگاه آزمایشی، بیلد و
انتشار سایت و پاکسازی کامل. **لایهٔ نوشتاری فقط با انتخاب صریح اپراتور اجرا
می‌شود** — PARASTOO_E2E_WRITE=1 — تصمیم لحظه‌ای اپراتور است، نه حالت سرور
(فرمان مالک ۲۰۲۶-۱۱-۱۲: مفهوم دو-حالتی حذف شد؛ خط قرمز ۴ سند: هیچ نوشتن‌حالتی
روی دادهٔ کاربران واقعی).

لایه‌ها:
  L0 خواندنی (همیشه)         — نسخه/کامیت، لندینگ، فرم ورود، گاردها
  L1 چرخهٔ محصول (صریح)       — register → project → wallet(mock) → [build+publish] → cleanup

کاربرد:
  python3 tests/e2e_user.py                       # خودکار: L0 همیشه؛ L1 فقط اگر PARASTOO_E2E_WRITE=1
  python3 tests/e2e_user.py --fast                # L1 بدون انتظار بیلد/انتشار (سریع)
  python3 tests/e2e_user.py --keep                # پاکسازی انجام نشود (اشکال‌زدایی)
  python3 tests/e2e_user.py --base http://127.0.0.1:8765   # نصب محلی/صریح
  python3 tests/e2e_user.py --build-timeout 600   # سقف انتظار بیلد (پیش‌فرض ۴۲۰ث)

متغیرهای محیطی: PARASTOO_PANEL_URL (آدرس پنل)، PARASTOO_E2E_WRITE=1
(اجبار اجرای L1 — انتخاب صریح اپراتور؛ مثلاً روی نصب محلی بدون /api/version).

خروجی: چک‌به‌چک فارسی + جمع‌بندی؛ کد خروج 0 فقط اگر هیچ FAIL نباشد.
کاربر تست با پیشوند agent- ساخته می‌شود و در پایان (مگر --keep) پاک می‌شود.
"""
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

DEFAULT_PANEL = "http://panel.193.151.142.128.nip.io"
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# کاربر تست: ایمیل/نام/رمز — هرگز روی کاربر واقعی نوشته نمی‌شود
SUFFIX = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
USER_EMAIL = f"agent-{SUFFIX}@test.local"
USER_NAME = f"agent-{SUFFIX}"
USER_PASS = "agent-pass-1"
PROJ_SLUG = f"agent-p-{SUFFIX}"     # الگوی معتبر slug: لاتین کوچک/عدد/خط‌تیره
SITE_SLUG = f"agent-s-{SUFFIX}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Browser:
    """نشست کوکی‌دار با دنبال‌کردن دستی ریدایرکت‌ها تا کلید فلش (m=) گم نشود."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar))
        self.flashes: list[str] = []

    def req(self, method: str, path: str, data=None, timeout: int = 20):
        """درخواست تکی؛ path مطلق هم می‌پذیرد (برای آدرس سایت منتشرشده).
        خطای شبکه (DNS/اتصال) به‌جای کرش، وضعیت 0 برمی‌گرداند تا چک FAIL شود
        و مهم‌تر: finally پاکسازی همیشه فرصت اجرا داشته باشد."""
        url = path if path.startswith("http") else f"{self.base}{path}"
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
        h = {"Content-Type": "application/x-www-form-urlencoded"} if isinstance(data, dict) else {}
        r = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with self.opener.open(r, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read() or b""
        except (urllib.error.URLError, OSError) as e:
            return 0, {}, str(e).encode()

    def go(self, method: str, path: str, data=None, max_hops: int = 6):
        """درخواست + دنبال‌کردن ریدایرکت‌ها؛ کلیدهای m= در self.flashes جمع می‌شود."""
        st, hdrs, body = self.req(method, path, data)
        for _ in range(max_hops):
            if st not in (301, 302, 303, 307, 308):
                break
            loc = hdrs.get("Location") or hdrs.get("location") or ""
            m = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("m", [])
            self.flashes.extend(m)
            st, hdrs, body = self.req("GET", loc)
        return st, body

    def csrf(self, path: str) -> str:
        """فیلد مخفی csrf از یک صفحهٔ واردشده (POSTهای واردشده الزامی‌اش دارند)."""
        _, body = self.go("GET", path)
        m = re.search(rb'name="csrf" value="([^"]+)"', body)
        return m.group(1).decode() if m else ""

    def last_flash(self) -> str:
        return self.flashes[-1] if self.flashes else ""


def _apps_domain(base: str) -> str:
    """دامنهٔ اپ‌ها از آدرس پنل: panel.X.nip.io → apps.X.nip.io."""
    host = urllib.parse.urlparse(base).netloc.split(":")[0]
    if host.startswith("panel."):
        return f"apps.{host[len('panel.'):]}"

    return "apps.127.0.0.1.nip.io"


def main() -> int:
    args = sys.argv[1:]
    fast = "--fast" in args
    keep = "--keep" in args
    bt = 420
    if "--build-timeout" in args:
        i = args.index("--build-timeout")
        bt = int(args[i + 1]) if i + 1 < len(args) else bt
        del args[i:i + 2]
    base = (next((a for a in args if not a.startswith("-")), None)
            or os.environ.get("PARASTOO_PANEL_URL") or DEFAULT_PANEL).rstrip("/")
    force_dev = os.environ.get("PARASTOO_E2E_WRITE") == "1"

    print(f"# هارنس کاربر-محور پرستو — {base}")
    if fast:
        print("# حالت --fast: بیلد/انتشار آزموده نمی‌شود")
    results: list[tuple[str, str]] = []
    b = Browser(base)

    def check(cid: str, label: str, state: str, note: str = ""):
        results.append((state, cid))
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "−"}[state]
        print(f"[{icon}] {cid:<16} {label}" + (f" — {note}" if note else ""))

    _last_net_err = [""]

    def _final_status(url: str, hops: int = 3) -> int:
        """GET با پیگیری ریدایرکت‌ها تا وضعیت نهایی.

        میدان 2026-11-10: ترافیکِ سایت‌های منتشرشده ۳۰۸/۳۰۱ به https وایلدکارت
        می‌دهد؛ وضعیت خامِ http دیگر معیار نیست — پیگیری تا 200/404 واقعی.
        """
        st, hdrs, body = b.req("GET", url)
        for _ in range(hops):
            if st == 0:
                _last_net_err[0] = body.decode(errors="replace")[:120]
                return st
            if st not in (301, 302, 303, 307, 308):
                return st
            loc = hdrs.get("Location") or hdrs.get("location") or ""
            if not loc:
                return st
            st, hdrs, body = b.req("GET", loc)
        return st

    # ---------- L0: خواندنی (همیشه) ----------
    st, _, body = b.req("GET", "/api/version")
    if st == 200:
        try:
            d = json.loads(body)
            check("mode", "نسخه/کامیت مستقر از /api/version", PASS,
                  f"version={d.get('version')} commit={str(d.get('commit'))[:12]}")
        except Exception as e:
            check("mode", "خواندن /api/version", FAIL, f"بدنهٔ نامعتبر: {e}")
    else:
        if force_dev:
            check("mode", "نسخه (نصب محلی بدون /api/version)", SKIP, "— با E2E_WRITE ادامه")
        else:
            check("mode", "/api/version در دسترس نیست", FAIL, f"HTTP {st} {body[:60].decode(errors='replace')}")
    write_ok = force_dev  # L1 فقط با انتخاب صریح اپراتور — نه حالت سرور (v0.5.29)

    def c_landing():
        st, _, _ = b.req("GET", "/")
        return (PASS, f"HTTP {st}") if st in (200, 302, 303) else (FAIL, f"HTTP {st}")

    def c_login_form():
        st, _, body = b.req("GET", "/login")
        ok = st == 200 and b'action="/login"' in body and b'name="password"' in body
        return (PASS, "فرم ورود سالم") if ok else (FAIL, f"HTTP {st} / فرم ناقص")

    def c_gate_anon():
        st, _, _ = b.req("GET", "/dashboard")
        return (PASS, f"HTTP {st} → مسدود") if st in (302, 303) else (FAIL, f"HTTP {st}")

    check("landing", "لندینگ در دسترس", *c_landing())
    check("login-form", "فرم ورود سالم", *c_login_form())
    check("gate-anon", "داشبورد برای ناشناس بسته است", *c_gate_anon())

    # ---------- L1: چرخهٔ محصول (فقط با انتخاب صریح — PARASTOO_E2E_WRITE=1) ----------
    if not write_ok:
        print("[−] L1-product      چرخهٔ کاربر رد شد — PARASTOO_E2E_WRITE=1 نیست (خط قرمز ۴ سند: نوشتن روی داده فقط با انتخاب صریح).")
        print("    برای اجرای کامل: PARASTOO_E2E_WRITE=1 python3 tests/e2e_user.py (کاربران تست agent-*؛ پاکسازی خودکار).")
        results.append((SKIP, "L1-product"))
    else:
        artifacts = {"project": False}
        try:
            # ۱) ثبت‌نام → ورود خودکار (فرم ثبت‌نام/ورود csrf ندارند — §۵ سند)
            b.go("POST", "/register", {"email": USER_EMAIL, "name": USER_NAME,
                                       "password": USER_PASS})
            ok = b.last_flash() == "reg_ok"
            check("register", f"ثبت‌نام {USER_EMAIL}", PASS if ok else FAIL,
                  f"flash={b.last_flash() or '?'}")

            # ۲) داشبورد واردشده + انسداد ادمین برای کاربر عادی
            # (نوار بالا نام کاربر را نشان می‌دهد: {{ user.name or user.email }})
            st, body = b.go("GET", "/dashboard")
            ok = st == 200 and USER_NAME.encode() in body
            check("dashboard", "داشبورد کاربر واردشده", PASS if ok else FAIL,
                  f"HTTP {st}")
            b.go("GET", "/admin")
            ok = b.last_flash() == "denied"
            check("admin-gate", "کاربر عادی از /admin رد می‌شود", PASS if ok else FAIL,
                  f"flash={b.last_flash() or '?'}")

            # ۳) ساخت پروژه (استاتیک — بیلدش سبک‌ترین است)
            csrf = b.csrf("/projects/new")
            b.go("POST", "/projects/new",
                 {"csrf": csrf, "name": "پروژهٔ عامل", "slug": PROJ_SLUG,
                  "description": "تست خودکار هارنس کاربر-محور"})
            # فلش project_created = مخزن همگام ساخته شد؛ git_pending = پروژه
            # ساخته شد و مخزن در پس‌زمینه (نصب بدون گیتا/ورکر) — هر دو موفق‌اند.
            ok = b.last_flash() in ("project_created", "git_pending")
            artifacts["project"] = ok
            check("project-create", f"ساخت پروژهٔ {PROJ_SLUG}", PASS if ok else FAIL,
                  f"flash={b.last_flash() or '?'}")

            # ۴) کیف پول: شارژ با درگاه آزمایشی — جریان کامل + idempotency
            wpage = b.go("GET", "/wallet")[1]
            m = re.search(rb'name="amount"[^>]*min="(\d+)"', wpage)
            amount = int(m.group(1)) if m else 10000
            mc = re.search(rb'name="csrf" value="([^"]+)"', wpage)
            if not mc:
                check("wallet-cycle", "صفحهٔ کیف پول فرم شارژ ندارد", FAIL, "csrf یافت نشد")
            else:
                st2, hdrs2, _ = b.req("POST", "/wallet/charge",
                                      {"csrf": mc.group(1).decode(),
                                       "amount": str(amount), "gateway": "mock"})
                pay_path = hdrs2.get("Location") or hdrs2.get("location") or ""
                ok = st2 == 303 and pay_path.startswith("/pay/mock/")
                if not ok:
                    check("wallet-cycle", "شروع شارژ → ریدایرکت به درگاه", FAIL,
                          f"HTTP {st2} loc={pay_path[:60]}")
                else:
                    token = pay_path.rsplit("/", 1)[-1]
                    st3, body3 = b.go("GET", pay_path)
                    mc2 = re.search(rb'name="csrf" value="([^"]+)"', body3)
                    form_ok = st3 == 200 and mc2 and f"/pay/mock/{token}/result".encode() in body3
                    if not form_ok:
                        check("wallet-cycle", "صفحهٔ درگاه آزمایشی", FAIL,
                              f"HTTP {st3} / فرم ناقص")
                    else:
                        b.go("POST", f"/pay/mock/{token}/result",
                             {"csrf": mc2.group(1).decode(), "result": "paid"})
                        paid = b.last_flash() == "pay_done"
                        # پخش دوبارهٔ callback باید با pay_dup رد شود (idempotent)
                        b.go("GET", f"/payment/callback/mock?token={token}&status=paid")
                        dup = b.last_flash() == "pay_dup"
                        wpage2 = b.go("GET", "/wallet")[1]
                        shown = f"{amount:,}".encode() in wpage2
                        allok = paid and dup and shown
                        check("wallet-cycle", f"شارژ {amount:,} تومان با درگاه آزمایشی + pay_dup",
                              PASS if allok else FAIL,
                              f"pay_done={paid} dup={dup} balance_shown={shown}")

            # ۵) بیلد + انتشار (مگر --fast): publish تا آماده‌شدن ایمیج تکرار می‌شود
            if not fast:
                deadline = time.time() + bt
                pub_flash = None
                while time.time() < deadline:
                    csrf = b.csrf(f"/projects/{PROJ_SLUG}")
                    b.flashes.clear()
                    b.go("POST", f"/projects/{PROJ_SLUG}/publish",
                         {"csrf": csrf, "site_slug": SITE_SLUG, "tier": "nano", "port": "80"})
                    pub_flash = b.last_flash()
                    if pub_flash != "image_missing":
                        break
                    print(f"    … ایمیج هنوز آماده نیست — {int(deadline - time.time())}ث مانده")
                    time.sleep(15)
                ok = pub_flash == "published"
                check("publish", f"انتشار سایت {SITE_SLUG}", PASS if ok else FAIL,
                      f"flash={pub_flash}")
                # پنجرهٔ آمادگی HTTPS سایت تازه میدان 2026-11-10: تا ~۱۰ دقیقه متغیر
                # (اتصال گواهی وایلدکارت به روتر تازهٔ لیبلی ترافیک) — سقف ۶۰۰ث
                deadline = time.time() + 600
                st = 0
                while time.time() < deadline:
                    st = _final_status(f"http://{SITE_SLUG}.apps.{_apps_domain(base)}")
                    if st == 200:
                        break
                    time.sleep(10)
                ok = st == 200
                check("site-live", "سایت منتشرشده 200 می‌دهد (با پیگیری ریدایرکت TLS، پنجرهٔ گواهی تا ۶۰۰ث)", PASS if ok else FAIL,
                      f"HTTP {st}" + (f" | last_net_err={_last_net_err[0]}" if st == 0 else ""))
            else:
                results.append((SKIP, "publish"))
                results.append((SKIP, "site-live"))
                print("[−] publish/site-live — پرش با --fast")

            # ۶) خروج — چک گیت بعد از خروج بدون دنبال‌کردن ریدایرکت (خودِ 303 شاهد است)
            b.go("GET", "/logout")
            st2, _, _ = b.req("GET", "/dashboard")
            ok = b.last_flash() == "logged_out" and st2 in (302, 303)
            check("logout", "خروج + بسته‌شدن داشبورد", PASS if ok else FAIL,
                  f"flash={b.last_flash() or '?'} dashboard_http={st2}")
        finally:
            # ۷) پاکسازی (قاعدهٔ §۵ سند: پروژه/سایت تستی در پایان همان چرخه پاک شود)
            if artifacts["project"] and not keep:
                try:
                    # اگر چرخه به خروج رسیده باشد نشست مرده است — برای پاکسازی دوباره وارد شو
                    b.go("POST", "/login", {"email": USER_EMAIL, "password": USER_PASS})
                    csrf = b.csrf(f"/projects/{PROJ_SLUG}")
                    b.flashes.clear()
                    b.go("POST", f"/projects/{PROJ_SLUG}/delete",
                         {"csrf": csrf, "confirm": PROJ_SLUG})
                    cleaned = b.last_flash() == "project_deleted"
                    if fast:
                        ok = cleaned
                        note = f"flash={b.last_flash() or '?'}"
                    else:
                        # مثل بالا: بعد از حذف، SNI تا بستنِ پنجرهٔ گواهی ممکن است
                        # چند دقیقه default بدهد — تا ۶۰۰ث منتظر 404 واقعی
                        deadline = time.time() + 600
                        st = 0
                        while time.time() < deadline:
                            st = _final_status(f"http://{SITE_SLUG}.apps.{_apps_domain(base)}")
                            if st == 404:
                                break
                            time.sleep(10)
                        ok = cleaned and st == 404
                        note = f"flash={b.last_flash() or '?'} site_http={st}" + (f" | last_net_err={_last_net_err[0]}" if st == 0 else "")
                    check("cleanup", "حذف پروژه (+404شدن سایت)", PASS if ok else FAIL, note)
                except Exception as e:
                    check("cleanup", "پاکسازی", FAIL, f"استثنا: {e}")
            elif keep:
                results.append((SKIP, "cleanup"))
                print(f"[−] cleanup — پرش با --keep (پروژهٔ {PROJ_SLUG} باقی است!)")
            else:
                results.append((SKIP, "cleanup"))
                print("[−] cleanup — پروژه ساخته نشد که پاک شود")

    npass = sum(1 for s, *_ in results if s == PASS)
    nfail = sum(1 for s, *_ in results if s == FAIL)
    nskip = sum(1 for s, *_ in results if s == SKIP)
    print(f"# جمع‌بندی: {npass} سبز، {nfail} قرمز، {nskip} ردشده — "
          f"{'چرخهٔ کاربر سالم ✓' if nfail == 0 else 'لازم به بررسی ✗'}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
