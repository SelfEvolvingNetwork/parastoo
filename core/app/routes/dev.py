"""حالت توسعه‌دهنده (v0.2.12) + پل دسترسی عامل (v0.3.7): گزارش زنده، لاگ در حافظه،
تاریخچهٔ آپدیت خودکار، و سه ابزار «دست باز» برای عامل هوشمند (exec/netcheck/sql).

قواعد دسترسی:
- وقتی PARASTOO_DEV_MODE=false (حالت استفاده): همهٔ مسیرهای /dev و /api/dev/*
  404 می‌دهند — نه 403 — تا حتی وجودشان لو نرود.
- وقتی روشن است: اگر PARASTOO_DEV_TOKEN در .env ست شده باشد، هدر X-Dev-Token
  (یا ?token=) کافی است؛ در غیر این صورت فقط ادمینِ واردشده به پنل.

پل دسترسی عامل (در حالت توسعه؛ در حالت تجاری 404):
- GET  /api/dev/netcheck بررسی واقعی دسترسی سرور به سرویس‌های جهانی (بدون حدس)
- POST /api/dev/sql      پرس‌وجوی فقط‌خواندنی (SELECT/WITH) روی دیتابیس
منطق هر دو در services/agentops.py است؛ اینجا فقط ورودی/خروجی وب + گارد.

تاریخچهٔ exec: مسیر POST /api/dev/exec (v0.3.7~v0.3.10، معادل SSH) در v0.3.11 به
دستور مالک کامل حذف شد — تنها مسیر اجرای دستور عامل روی سرور، ایجنت دسترسی مستقل
(access-agent) است؛ پنجرهٔ موقت بدون توکن v0.3.10 هم با آن حذف شد.

زیرساخت رینگ‌بافر لاگ از v0.3.0 در devlog.py است (main.py هم برای لاگ خطاهای
۵۰۰ همان را استفاده می‌کند).
"""
import json
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from .. import devlog, dockerops, gitea
from ..config import settings
from ..db import SessionLocal
from ..models import Build, Project, Site, User
from ..deps import ctx, render, resolve_user
from ..services import agentops

router = APIRouter()

APP_STARTED = time.time()

devlog.attach()  # idempotent — حتی اگر main هنوز import نشده باشد


def _session_user(request: Request) -> User | None:
    db = SessionLocal()
    try:
        return resolve_user(request, db)
    finally:
        db.close()


def _dev_guard(request: Request) -> None:
    """حالت توسعه خاموش یا دسترسی مجاز نیست → 404 (نه 403)."""
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="not found")
    if settings.DEV_TOKEN:
        given = request.headers.get("X-Dev-Token") or request.query_params.get("token") or ""
        if given == settings.DEV_TOKEN:
            return
    user = _session_user(request)
    if user is not None and user.is_admin:
        return
    raise HTTPException(status_code=404, detail="not found")


# ---------- جمع‌کردن گزارش ----------

def _update_history(limit: int = 20) -> list[dict]:
    """تاریخچهٔ آپدیت خودکار از logs/updates.jsonl که از هاست سوار شده است."""
    path = Path("/app/host-logs/updates.jsonl")
    if not path.is_file():
        path = Path(settings.DATA_DIR) / "host-logs" / "updates.jsonl"  # حالت محلی/تست
    out: list[dict] = []
    try:
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            for line in lines[-limit:]:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    out.append({"raw": line[:300]})
    except Exception as e:  # گزارش هرگز نباید خودش خطا بدهد
        out.append({"error": str(e)})
    out.reverse()  # تازه‌ترین اول
    return out


def _containers() -> list[dict]:
    try:
        cli = dockerops.client()
        out = []
        for c in cli.containers.list(all=True):
            name = (c.name or "")
            if not (name.startswith("parastoo-")):
                continue
            out.append({
                "name": name,
                "status": c.status,
                "image": (c.image.tags or ["?"])[0] if c.image else "?",
            })
        return sorted(out, key=lambda x: x["name"])
    except Exception as e:
        return [{"error": str(e)}]


def _db_ok() -> bool:
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        return True
    except Exception:
        return False


def _counts_and_failures() -> dict:
    out: dict = {"error": None}
    try:
        db = SessionLocal()
        out["users"] = db.query(User).count()
        out["projects"] = db.query(Project).count()
        out["sites_running"] = db.query(Site).filter(Site.status == "running").count()
        out["builds_failed_total"] = db.query(Build).filter(Build.status == "failed").count()
        failed = (db.query(Build, Project)
                  .join(Project, Build.project_id == Project.id)
                  .filter(Build.status == "failed")
                  .order_by(Build.id.desc()).limit(5).all())
        out["recent_failed_builds"] = [
            {"build_id": b.id, "project": p.slug, "commit": b.commit_sha[:12],
             "created_at": str(b.created_at), "log_tail": (b.log or "")[-400:]}
            for b, p in failed
        ]
        db.close()
    except Exception as e:
        out["error"] = str(e)
    return out


def collect_report() -> dict:
    """گزارش کامل وضعیت — همان چیزی که /api/dev/report و صفحهٔ /dev نشان می‌دهند."""
    import os
    import sys
    report = {
        "app": {
            "version": settings.VERSION,
            "commit": settings.DEPLOYED_COMMIT or None,
            "deployed_at": settings.DEPLOYED_AT or None,
            "dev_mode": settings.DEV_MODE,
            "python": sys.version.split()[0],
            "uptime_s": int(time.time() - APP_STARTED),
            "pid": os.getpid(),
        },
        "flags": {
            "base_domain": settings.BASE_DOMAIN,
            "panel_domain": settings.PANEL_DOMAIN,
            "tls": settings.TLS_ENABLED,
            "register_open": settings.REGISTER_OPEN,
            "workers_disabled": settings.DISABLE_WORKERS,
            "timezone": settings.TIMEZONE,
            # هیچ رمزی اینجا برنمی‌گردد — فقط پرچم‌ها
        },
        "health": {
            "db": _db_ok(),
            "docker": dockerops.available(),
            "gitea": gitea.GiteaClient().healthy(),
            "disk_percent": int(shutil.disk_usage("/").used / shutil.disk_usage("/").total * 100)
            if shutil.disk_usage("/").total else None,
        },
        "containers": _containers(),
        "docker_stats": dockerops.system_stats() if dockerops.available() else None,
        "updates": _update_history(),
        "errors": devlog.recent_errors(),
    }
    report["usage"] = _counts_and_failures()
    return report


# ---------- مسیرها ----------

@router.get("/api/dev/report")
def dev_report(request: Request):
    _dev_guard(request)
    return JSONResponse(collect_report())


@router.get("/api/dev/logs")
def dev_logs(request: Request, tail: int = 200):
    _dev_guard(request)
    tail = max(1, min(tail, devlog.LOG_CAPACITY))
    return PlainTextResponse("\n".join(devlog.tail(tail)) or "(no logs yet)")


@router.get("/api/dev/updates")
def dev_updates(request: Request):
    _dev_guard(request)
    return JSONResponse({"ok": True, "updates": _update_history(50)})


@router.get("/dev")
def dev_page(request: Request):
    _dev_guard(request)
    user = _session_user(request)
    return render("dev.html", ctx(request, user, report=collect_report()))


# ---------- پل دسترسی عامل (در حالت توسعه؛ همان گارد بالا) ----------

# تاریخچه: POST /api/dev/exec + پنجرهٔ موقت بدون توکن (v0.3.7~v0.3.10) در v0.3.11
# کامل حذف شد (دستور مالک: «قابلیت ssh را از پرستو کاملاً حذف کن») — چون access-agent
# روی همین سرور نصب و راستی‌آزمایی شده و از این پس مسیر اجرای دستور عامل همان است.

# ---------- مسیرها — netcheck ----------

@router.get("/api/dev/netcheck")
def dev_netcheck(request: Request, url: str = "", groups: str = ""):
    _dev_guard(request)
    grp = [g.strip() for g in groups.split(",") if g.strip()] if groups else None
    try:
        res = agentops.netcheck(groups=grp, url=url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    agentops.audit("netcheck", {"url": url[:200], "groups": groups[:200],
                                "n_ok": res.get("n_ok"), "n_total": res.get("n_total")})
    return JSONResponse(res)


class SqlIn(BaseModel):
    sql: str
    limit: int = 100


@router.post("/api/dev/sql")
def dev_sql(request: Request, body: SqlIn):
    _dev_guard(request)
    try:
        res = agentops.readonly_sql(body.sql, limit=body.limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    agentops.audit("sql", {"sql": (body.sql or "")[:200], "n_rows": res.get("n_rows")})
    return JSONResponse({"ok": True, **res})
