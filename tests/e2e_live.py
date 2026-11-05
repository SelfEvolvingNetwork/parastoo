#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""راستی‌آزمایی زندهٔ پرستو — لایهٔ عمومی (+ لایهٔ توسعه‌دهنده اگر توکن باشد).

این هارنس استانداردِ گام M6 از docs/AI_AGENT_LOOP.md است: بعد از هر استقرار،
وضعیت سرور واقعی را «از چشم بیرون» آزمود و بدون هیچ POST تغییردهنده‌ای خارج می‌شود.
فقط یک POST ردشدنی زده می‌شود (وب‌هوک بدون امضا) که صرفاً رفتار گارد را می‌سنجد.

کاربرد:
  python3 tests/e2e_live.py                          # آدرس از env یا پیش‌فرض سند
  python3 tests/e2e_live.py http://panel.X.nip.io    # آدرس صریح
  python3 tests/e2e_live.py --expect-commit <SHA>    # تأیید استقرارِ کامیت مشخص (گام M5)

متغیرهای محیطی:
  PARASTOO_PANEL_URL   آدرس پنل زنده (پیش‌فرض: مقدار ثابت سند)
  PARASTOO_DEV_TOKEN   اگر ست باشد، لایهٔ /api/dev/* هم آزموده می‌شود (X-Dev-Token)

خروجی: چک‌به‌چک فارسی + جمع‌بندی؛ کد خروج 0 فقط اگر هیچ چکی FAIL نباشد.
SKIP خطا نیست (مثلاً لایهٔ توسعه‌دهنده بدون توکن).
"""
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_PANEL = "http://panel.193.151.142.128.nip.io"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # نمی‌خواهیم 30x را دنبال کنیم
        return None


def fetch(method: str, url: str, data=None, headers=None, timeout: int = 15):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read() or b""
    # URLError (قطع شبکه/DNS) عمداً به بالا پرتاب می‌شود — چکِ احاطه‌کننده آن را FAIL می‌کند


def main() -> int:
    args = sys.argv[1:]
    expect_commit = None
    if "--expect-commit" in args:
        i = args.index("--expect-commit")
        expect_commit = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    base = (next((a for a in args if not a.startswith("-")), None)
            or os.environ.get("PARASTOO_PANEL_URL") or DEFAULT_PANEL).rstrip("/")
    dev_token = os.environ.get("PARASTOO_DEV_TOKEN", "").strip()
    timeout = 15

    print(f"# راستی‌آزمایی زندهٔ پرستو — {base}")
    if expect_commit:
        print(f"# انتظار استقرار کامیت: {expect_commit[:12]}")
    results: list[tuple[str, str, str]] = []  # (state, id, note)

    def check(cid: str, label: str, fn):
        try:
            state, note = fn()
        except Exception as e:
            state, note = FAIL, f"استثنا: {e}"
        results.append((state, cid, ""))
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "−"}[state]
        print(f"[{icon}] {cid:<14} {label}" + (f" — {note}" if note else ""))

    # ---------- لایهٔ عمومی ----------
    def c_version():
        st, _, body = fetch("GET", f"{base}/api/version", timeout=timeout)
        if st != 200:
            return FAIL, f"HTTP {st}"
        d = json.loads(body)
        if not d.get("ok") or d.get("app") != "parastoo":
            return FAIL, f"بدنهٔ غیرمنتظره: {str(d)[:120]}"
        note = f"version={d.get('version')} commit={str(d.get('commit'))[:12]} dev_mode={d.get('dev_mode')}"
        if expect_commit and (d.get("commit") or "") != expect_commit:
            return FAIL, f"کامیت مستقر {str(d.get('commit'))[:12]} ≠ انتظار {expect_commit[:12]} ({note})"
        return PASS, note

    def c_healthz():
        st, _, body = fetch("GET", f"{base}/healthz", timeout=timeout)
        if st != 200:
            return FAIL, f"HTTP {st}"
        d = json.loads(body)
        return (PASS, "db ok") if d.get("ok") else (FAIL, "ok=false")

    def c_landing():
        st, _, _ = fetch("GET", f"{base}/", timeout=timeout)
        return (PASS, f"HTTP {st}") if st in (200, 302, 303) else (FAIL, f"HTTP {st}")

    def c_login_page():
        st, _, body = fetch("GET", f"{base}/login", timeout=timeout)
        if st != 200:
            return FAIL, f"HTTP {st}"
        # فرم ورود عمداً csrf ندارد (CSRF فقط برای نشستِ واردشده) — فرم و فیلدها را می‌سنجیم
        ok = b'action="/login"' in body and b'name="password"' in body
        return (PASS, "فرم ورود سالم") if ok else (FAIL, "فرم ناقص")

    def c_register_page():
        st, _, _ = fetch("GET", f"{base}/register", timeout=timeout)
        return (PASS, f"HTTP {st}") if st in (200, 302, 303) else (FAIL, f"HTTP {st}")

    def _gate(path):
        st, _, _ = fetch("GET", f"{base}{path}", timeout=timeout)
        if st in (302, 303):
            return PASS, f"HTTP {st} → مسدود برای ناشناس"
        if st == 200:
            return FAIL, "HTTP 200 — صفحهٔ محافظت‌شده برای ناشناس باز است!"
        return FAIL, f"HTTP {st} (انتظار 302/303)"

    def c_dev_page_anon():
        st, _, _ = fetch("GET", f"{base}/dev", timeout=timeout)
        return (PASS, "404 — وجودش لو نمی‌رود") if st == 404 else (FAIL, f"HTTP {st} (انتظار 404)")

    def c_dev_api_anon():
        st, _, _ = fetch("GET", f"{base}/api/dev/report", timeout=timeout)
        return (PASS, "404 — گارد حالت توسعه") if st == 404 else (FAIL, f"HTTP {st} (انتظار 404)")

    def c_static():
        st, _, body = fetch("GET", f"{base}/static/style.css", timeout=timeout)
        if st != 200:
            return FAIL, f"HTTP {st}"
        return (PASS, f"{len(body)} بایت") if body else FAIL

    def c_headers():
        st, hdrs, _ = fetch("GET", f"{base}/login", timeout=timeout)
        low = {k.lower(): v for k, v in hdrs.items()}
        missing = [h for h in ("x-frame-options", "x-content-type-options", "referrer-policy")
                   if not low.get(h)]
        if st != 200:
            return FAIL, f"HTTP {st}"
        if missing:
            return FAIL, f"هدر غایب: {', '.join(missing)}"
        if "uvicorn" in (low.get("server") or "").lower():
            return FAIL, "هدر Server لو می‌دهد"
        return PASS, "۳ هدر امنیتی + بدون لو دادن Server"

    def c_webhook_reject():
        st, _, body = fetch("POST", f"{base}/api/hooks/gitea/1",
                         data=b"{}", headers={"Content-Type": "application/json"}, timeout=timeout)
        try:
            d = json.loads(body)
        except Exception:
            return FAIL, f"HTTP {st} بدون بدنهٔ JSON"
        # ردِ امضا معنایی است: HTTP 200 با ok=false (bad signature / unknown project)
        if d.get("ok") is False and d.get("error"):
            return PASS, f"رد شد: {d.get('error')}"
        return FAIL, f"HTTP {st} — پاسخ غیرمنتظره: {str(d)[:80]}"

    check("version", "/api/version — نسخه/کامیت/dev_mode", c_version)
    check("healthz", "/healthz — سلامت db", c_healthz)
    check("landing", "/ — لندینگ یا ریدایرکت", c_landing)
    check("login-page", "/login — فرم ورود سالم", c_login_page)
    check("register", "/register — در دسترس (یا بستهٔ تمیز)", c_register_page)
    check("gate-dashboard", "/dashboard ناشناس → ریدایرکت", lambda: _gate("/dashboard"))
    check("gate-admin", "/admin ناشناس → ریدایرکت", lambda: _gate("/admin"))
    check("dev-page-anon", "/dev ناشناس → 404", c_dev_page_anon)
    check("dev-api-anon", "/api/dev/report ناشناس → 404", c_dev_api_anon)
    check("static", "/static/style.css — استاتیک سالم", c_static)
    check("sec-headers", "هدرهای امنیتی", c_headers)
    check("webhook-reject", "وب‌هوک بدون امضا رد می‌شود (ok:false)", c_webhook_reject)

    # ---------- لایهٔ توسعه‌دهنده (فقط با توکن) ----------
    if not dev_token:
        print("[−] dev-report      لایهٔ توسعه‌دهنده رد شد — PARASTOO_DEV_TOKEN خالی است (برای چرخهٔ کامل §۲ سند)")
        results.append((SKIP, "dev-report", ""))
        print("[−] dev-updates     — (وابسته به توکن)")
        results.append((SKIP, "dev-updates", ""))
        print("[−] dev-agent       پل عامل exec/netcheck/sql — (وابسته به توکن)")
        results.append((SKIP, "dev-agent", ""))
    else:
        h = {"X-Dev-Token": dev_token}

        def c_dev_report():
            st, _, body = fetch("GET", f"{base}/api/dev/report", headers=h, timeout=timeout)
            if st != 200:
                return FAIL, f"HTTP {st} (توکن رد شد یا dev خاموش است)"
            d = json.loads(body)
            miss = [k for k in ("app", "health", "updates", "containers") if k not in d]
            if miss:
                return FAIL, f"کلید غایب: {miss}"
            return PASS, (f"commit={str(d['app'].get('commit'))[:12]} "
                          f"db={d['health'].get('db')} gitea={d['health'].get('gitea')} "
                          f"disk={d['health'].get('disk_percent')}%")

        def c_dev_updates():
            st, _, body = fetch("GET", f"{base}/api/dev/updates", headers=h, timeout=timeout)
            if st != 200:
                return FAIL, f"HTTP {st}"
            d = json.loads(body)
            updates = d.get("updates", [])
            if not updates:
                return PASS, "تاریخچهٔ آپدیت خالی"
            return PASS, f"{len(updates)} رکورد؛ آخرین: {str(updates[0])[:100]}"

        def c_dev_netcheck():
            st, _, body = fetch("GET", f"{base}/api/dev/netcheck?groups=github",
                                headers=h, timeout=30)
            if st != 200:
                return FAIL, f"HTTP {st}"
            d = json.loads(body)
            if not d.get("ok") or not d.get("targets"):
                return FAIL, f"پاسخ غیرمنتظره: {str(d)[:100]}"
            g_ok, g_all = (sum(1 for t in d["targets"] if t["url"].startswith("https://github")
                               and t.get("ok")), sum(1 for t in d["targets"] if t["url"].startswith("https://github")))
            return PASS, f"github: {g_ok}/{g_all} باز — کل {d.get('n_ok')}/{d.get('n_total')} ({d.get('duration_ms')}ms)"

        def c_dev_exec():
            st, _, body = fetch("POST", f"{base}/api/dev/exec", headers={**h, "Content-Type": "application/json"},
                                data=json.dumps({"cmd": "echo parastoo-agent-ok",
                                                 "timeout_s": 30}).encode(),
                                timeout=60)
            if st != 200:
                return FAIL, f"HTTP {st}"
            d = json.loads(body)
            if not d.get("ok"):
                return FAIL, f"ok=false: {str(d)[:100]}"
            if d.get("exit_code") != 0 or "parastoo-agent-ok" not in (d.get("stdout") or ""):
                return FAIL, f"rc={d.get('exit_code')} stdout={str(d.get('stdout'))[:80]}"
            return PASS, f"rc=0 ({d.get('duration_ms')}ms) — اجرای دستور روی هاست کار می‌کند"

        def c_dev_sql():
            st, _, body = fetch("POST", f"{base}/api/dev/sql", headers={**h, "Content-Type": "application/json"},
                                data=json.dumps({"sql": "SELECT count(*) AS n FROM users",
                                                 "limit": 10}).encode(),
                                timeout=timeout)
            if st != 200:
                return FAIL, f"HTTP {st}"
            d = json.loads(body)
            if not d.get("ok") or not d.get("rows"):
                return FAIL, f"پاسخ غیرمنتظره: {str(d)[:100]}"
            return PASS, f"کاربران دیتابیس: {d['rows'][0][0]}"

        check("dev-report", "/api/dev/report با X-Dev-Token", c_dev_report)
        check("dev-updates", "/api/dev/updates با X-Dev-Token", c_dev_updates)
        check("dev-netcheck", "/api/dev/netcheck — دسترسی جهانی از سرور", c_dev_netcheck)
        check("dev-exec", "/api/dev/exec — اجرای دستور روی هاست", c_dev_exec)
        check("dev-sql", "/api/dev/sql — پرس‌وجوی فقط‌خواندنی", c_dev_sql)

    npass = sum(1 for s, *_ in results if s == PASS)
    nfail = sum(1 for s, *_ in results if s == FAIL)
    nskip = sum(1 for s, *_ in results if s == SKIP)
    print(f"# جمع‌بندی: {npass} سبز، {nfail} قرمز، {nskip} ردشده — {'همه‌چیز سالم ✓' if nfail == 0 else 'لازم به بررسی ✗'}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
