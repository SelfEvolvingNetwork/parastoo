"""پل دسترسی عامل هوشمند در حالت توسعه (v0.3.7) — «دست باز» برای چرخهٔ تست/توسعه.

هدف: عامل هوشمند (AI Agent) هیچ‌چیز را «حدس» نزند؛ از طریق مسیرهای
`/api/dev/*` (فقط وقتی PARASTOO_DEV_MODE=true) می‌تواند:

1. **exec روی هاست** («SSH از طریق پرستو»): دستور دلخواه روی خود سرور اجرا شود و
   خروجی/کدخروج/مدت برگردد. اجرا بدون هیچ شلی از پروسهٔ هسته و فقط با Docker SDK
   در یک کانتینر کمکی کم‌عمرِ privileged انجام می‌شود که `/` هاست را در `/host`
   سوار و با `chroot` دستور را در فضای واقعی هاست می‌زند (پاسخ همان چیزی است که
   SSH مستقیم می‌داد — از جمله امکان ssh به سرورهای دیگر، وقتی کلید روی هاست باشد).
2. **exec داخل کانتینر هسته** — برای کارهای درون‌پلنی (مثلاً دسترسی به env هسته).
3. **netcheck** — بررسی واقعیِ دسترسی سرور (که در ایران است) به سرویس‌های جهانی
   (گیت‌هاب، Docker Hub، PyPI، npm، Let's Encrypt و پراکسی‌های تنظیم‌شده در .env)
   به‌همراه آدرس دلخواه — با تأخیر و وضعیت، تا عامل دربارهٔ آینه/پراکسی حدس نزند.
4. **SQL فقط‌خواندنی** — پرس‌وجوی SELECT روی دیتابیس برای بازرسی داده بدون حدس.

قواعد این ماژول:
- گارد دسترسی اینجاست؟ نه — این ماژول بدون گارد است؛ فقط routes/dev.py بعد از
  `_dev_guard` (و در حالت تجاری: هیچ‌وقت) باید صدایش بزند.
- هرگز subprocess/shell از پروسهٔ هسته زده نمی‌شود (قاعدهٔ طلایی dockerops) —
  همهٔ اجراها با Docker SDK است.
- هر فراخوانی exec با `audit()` به `DATA_DIR/dev/agent-audit.jsonl` ثبت می‌شود
  (best-effort؛ شکست ثبت هرگز جریان اصلی را نمی‌شکند).
- خروجی‌ها سقف دارند (حافظه/پهنای‌باند)؛ سقف زمان exec هم سمت Docker اعمال می‌شود.
"""
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .. import dockerops
from ..config import settings

# ---------- ثابت‌ها ----------

# ایمیج کانتینر کمکی exec هاست — روی هر نصب، ایمیج خودِ هسته لوکال وجود دارد
# (بیلد compose)؛ قابل بازنویسی با PARASTOO_EXEC_IMAGE برای نصب‌های خاص.
EXEC_IMAGE = os.environ.get("PARASTOO_EXEC_IMAGE", "parastoo/core:latest")

MAX_CMD_LEN = 8000          # سقف طول دستور exec (کاراکتر)
MAX_TIMEOUT_S = 600         # سقف زمان اجرای exec (ثانیه)
MAX_OUTPUT_CHARS = 200_000  # سقف هر کدام از stdout/stderr در پاسخ (دم نگه داشته می‌شود)
MAX_SQL_ROWS = 200          # سقف ردیف پاسخ SQL
MAX_CELL_CHARS = 300        # سقف طول هر سلول سریالایزشده

# گروه‌های پیش‌فرض netcheck — سرویس‌هایی که چرخهٔ نصب/آپدیت/بیلد پرستو واقعاً
# به آن‌ها وابسته است (SANCTIONS.md/README) + پراکسی‌هایی که در .env تنظیم شده‌اند.
NETCHECK_GROUPS: dict[str, list[str]] = {
    "github": [
        "https://github.com",
        "https://api.github.com",
        "https://raw.githubusercontent.com",
        "https://codeload.github.com",
        "https://objects.githubusercontent.com",
    ],
    "dockerhub": [
        "https://registry-1.docker.io/v2/",
        "https://auth.docker.io/token",
        "https://index.docker.io",
    ],
    "pip": [
        "https://pypi.org/simple/",
        "https://files.pythonhosted.org",
    ],
    "npm": [
        "https://registry.npmjs.org",
    ],
    "infra": [
        "https://get.docker.com",
        "https://download.docker.com",
        "https://acme-v02.api.letsencrypt.org/directory",
        "https://quay.io",
    ],
    "proxy": [
        "https://gh-proxy.com",
    ],
}


def _configured_proxies() -> list[str]:
    """پراکسی/آینه‌هایی که مدیر در .env تنظیم کرده — netcheck همان‌ها را می‌سنجد."""
    out = []
    for v in (os.environ.get("PARASTOO_GH_PROXY", ""),
              settings.PIP_INDEX_URL or "",
              settings.NPM_REGISTRY or ""):
        v = (v or "").strip()
        if v.startswith(("http://", "https://")) and v not in out:
            out.append(v)
    return out


# ---------- audit ----------

def audit(kind: str, payload: dict) -> None:
    """ثبت best-effort اقدامات عامل در DATA_DIR/dev/agent-audit.jsonl."""
    try:
        d = Path(settings.DATA_DIR) / "dev"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "kind": kind, **payload}
        with (d / "agent-audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit هرگز نباید خودش خطا بدهد


# ---------- exec ----------

def _trim(s: str) -> str:
    if s is None:
        return ""
    return s if len(s) <= MAX_OUTPUT_CHARS else "…\n" + s[-MAX_OUTPUT_CHARS:]


def run_host(cmd: str, timeout_s: int = 120) -> dict:
    """اجرای دستور روی خودِ هاست با کانتینر کمکی privileged + chroot (Docker SDK).

    معادل SSH روی سرور: کدخروج، stdout و stderr واقعی هاست برمی‌گردد.
    کانتینر همیشه حذف می‌شود (حتی در خطا/تایم‌اوت) — اثری روی هاست نمی‌ماند.
    """
    c = dockerops.client()  # DockerUnavailable اگر داکر نیست
    api = c.api
    t0 = time.time()
    hc = api.create_host_config(privileged=True, pid_mode="host",
                                binds={"/": {"bind": "/host", "mode": "rw"}})
    cont = api.create_container(
        image=EXEC_IMAGE,
        command=["chroot", "/host", "/bin/bash", "-lc", cmd],
        host_config=hc,
        environment={"HOME": "/root", "TERM": "dumb"},
        detach=True, tty=False, stdin_open=False,
    )
    cid = cont["Id"]
    timed_out = False
    try:
        api.start(cid)
        try:
            wait = api.wait(cid, condition="not-running", timeout=max(1, int(timeout_s)))
            rc = int(wait.get("StatusCode", -1))
        except Exception:
            timed_out = True
            rc = -1
            try:
                api.kill(cid)
            except Exception:
                pass
        try:
            # توجه: APIClient.logs در docker-py 7.x پارامتر demux ندارد —
            # دو فراخوانی جدا برای stdout/stderr (نسخه‌پذیر و بدون پارسِ دستی هدر استریم)
            out_b = api.logs(cid, stdout=True, stderr=False) or b""
            err_b = api.logs(cid, stdout=False, stderr=True) or b""
            if not isinstance(out_b, (bytes, bytearray)):
                out_b = b""
            if not isinstance(err_b, (bytes, bytearray)):
                err_b = b""
        except Exception as e:
            logging.getLogger("parastoo").warning("agent-exec logs fetch failed: %r", e)
            out_b, err_b = b"", b""
    finally:
        try:
            api.remove_container(cid, force=True, v=True)
        except Exception:
            pass
    return {
        "where": "host",
        "cmd_len": len(cmd),
        "exit_code": rc,
        "timed_out": timed_out,
        "stdout": _trim(out_b.decode(errors="replace")),
        "stderr": _trim(err_b.decode(errors="replace")),
        "duration_ms": int((time.time() - t0) * 1000),
    }


def run_in_core(cmd: str) -> dict:
    """اجرای دستور داخل کانتینر هسته (parastoo-core) — برای کارهای درون‌پلنی."""
    c = dockerops.client()
    t0 = time.time()
    try:
        container = c.containers.get("parastoo-core")
    except dockerops.NotFound:
        raise dockerops.DockerUnavailable("کانتینر هسته (parastoo-core) پیدا نشد")
    res = container.exec_run(["/bin/bash", "-lc", cmd], demux=True, workdir="/app")
    # demux هر جریان می‌تواند None باشد (مثلاً همهٔ خروجی روی stdout) — نرمال‌سازی:
    raw = res.output or (b"", b"")
    out_b = raw[0] if isinstance(raw[0], (bytes, bytearray)) else b""
    err_b = raw[1] if isinstance(raw[1], (bytes, bytearray)) else b""
    return {
        "where": "core",
        "cmd_len": len(cmd),
        "exit_code": res.exit_code or 0,
        "timed_out": False,
        "stdout": _trim(out_b.decode(errors="replace")),
        "stderr": _trim(err_b.decode(errors="replace")),
        "duration_ms": int((time.time() - t0) * 1000),
    }


# ---------- netcheck ----------

def probe_url(url: str, timeout_s: float = 8.0) -> dict:
    """یک آدرس را از منظر سرور می‌سنجد — DNS + یک GET واقعی.

    «ok» یعنی از شبکهٔ سرور پاسخ HTTP گرفتیم (هر وضعیتی — حتی 401/403/404،
    چون مثلاً registry-1.docker.io/v2/ سالم هم 401 می‌دهد)؛ خطای DNS/تایم‌اوت/
    اتصال یعنی دسترسی واقعاً بسته است.
    """
    t0 = time.time()
    out: dict = {"url": url, "ok": False, "status": None, "latency_ms": None,
                 "ip": None, "error": None}
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("scheme")
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(p.hostname, None)})
        out["ip"] = ips[0] if ips else None
    except ValueError:
        out["error"] = "آدرس نامعتبر (فقط http/https)"
        return out
    except Exception as e:
        out["error"] = f"DNS: {type(e).__name__}"
        return out
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "parastoo-netcheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            r.read(64)
            out["ok"], out["status"] = True, r.status
    except urllib.error.HTTPError as e:
        out["ok"], out["status"] = True, e.code  # پاسخ HTTP رسید → مسیر باز است
    except Exception as e:
        out["error"] = type(e).__name__
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def netcheck(groups: list[str] | None = None, url: str = "",
             timeout_s: float = 8.0) -> dict:
    """بررسی دسترسی جهانی از داخل سرور — گروه‌های preset + آدرس دلخواه."""
    t0 = time.time()
    targets: list[str] = []
    if url:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("url باید با http:// یا https:// شروع شود")
        targets.append(url)
    if groups:
        unknown = [g for g in groups if g not in NETCHECK_GROUPS and g != "configured"]
        if unknown:
            raise ValueError(f"گروه ناشناخته: {', '.join(unknown)}")
        for g in groups:
            if g == "configured":
                targets += [u for u in _configured_proxies() if u not in targets]
            else:
                targets += [u for u in NETCHECK_GROUPS[g] if u not in targets]
    if not targets:
        raise ValueError("هیچ هدفی انتخاب نشد (groups یا url بدهید)")
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as ex:
        futs = {ex.submit(probe_url, u, timeout_s): u for u in targets}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:  # هرگز کل گزارش را نسوزان
                results.append({"url": futs[f], "ok": False, "status": None,
                                "latency_ms": None, "ip": None, "error": type(e).__name__})
    results.sort(key=lambda r: r["url"])
    return {
        "ok": True,
        "targets": results,
        "n_ok": sum(1 for r in results if r["ok"]),
        "n_total": len(results),
        "duration_ms": int((time.time() - t0) * 1000),
    }


# ---------- SQL فقط‌خواندنی ----------

_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|vacuum|call|do|reindex|comment|lock|listen|notify)\b",
    re.IGNORECASE,
)


def readonly_sql(sql: str, limit: int = 100) -> dict:
    """اجرای یک پرس‌وجوی فقط‌خواندنی (SELECT/WITH) — همیشه rollback؛ هیچ تغییری."""
    from sqlalchemy import text as sa_text
    from ..db import SessionLocal

    sql = (sql or "").strip()
    if not sql:
        raise ValueError("پرس‌وجو خالی است")
    if re.search(r";\s*\S", sql):  # «;» در وسط = چند دستور
        raise ValueError("فقط یک دستور مجاز است («;» اضافه حذف کنید)")
    first = re.match(r"^\s*(\w+)", sql)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise ValueError("فقط SELECT یا WITH — این مسیر فقط‌خواندنی است")
    if _FORBIDDEN_RE.search(sql):
        raise ValueError("کلیدواژهٔ تغییردهنده در پرس‌وجو یافت شد؛ مسیر فقط‌خواندنی است")
    limit = max(1, min(int(limit or 100), MAX_SQL_ROWS))

    db = SessionLocal()
    try:
        res = db.execute(sa_text(sql))
        keys = list(res.keys())
        rows = res.fetchmany(limit + 1)
        has_more = len(rows) > limit
        rows = rows[:limit]
        data = [[_cell(v) for v in r] for r in rows]
        return {"columns": keys, "rows": data, "n_rows": len(data),
                "has_more": has_more}
    finally:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def _cell(v):
    """سلول را برای JSON آماده می‌کند — انواع اصلی (int/float/bool) حفظ می‌شوند."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, (datetime,)):
        return v.isoformat(sep=" ")
    s = str(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "…"
