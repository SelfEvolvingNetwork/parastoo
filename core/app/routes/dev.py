"""حالت توسعه‌دهنده (v0.2.12): گزارش زنده، لاگ در حافظه، تاریخچهٔ آپدیت خودکار.

قواعد دسترسی:
- وقتی PARASTOO_DEV_MODE=false (حالت استفاده): همهٔ مسیرهای /dev و /api/dev/*
  404 می‌دهند — نه 403 — تا حتی وجودشان لو نرود.
- وقتی روشن است: اگر PARASTOO_DEV_TOKEN در .env ست شده باشد، هدر X-Dev-Token
  (یا ?token=) کافی است؛ در غیر این صورت فقط ادمینِ واردشده به پنل.
"""
import logging
import shutil
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from .. import dockerops, gitea
from ..config import settings
from ..db import SessionLocal
from ..models import Build, Project, Site, User
from ..deps import COOKIE_NAME, ctx, render
from ..security import read_session

router = APIRouter()

APP_STARTED = time.time()
LOG_CAPACITY = 1000


class _Ring(logging.Handler):
    """آخرین N رکورد لاگ این پروسه در حافظه — منبع /api/dev/logs."""

    def __init__(self, capacity: int = LOG_CAPACITY):
        super().__init__()
        self.buf: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buf.append(self.format(record))
        except Exception:
            pass


_ring = _Ring()
for _name in ("", "parastoo", "uvicorn", "uvicorn.error"):
    _lg = logging.getLogger(_name)
    if not any(isinstance(h, _Ring) for h in _lg.handlers):
        _lg.addHandler(_ring)
logging.getLogger("parastoo").setLevel(logging.INFO)


def _session_user(request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    uid = read_session(token)
    if uid is None:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        return user if (user is not None and user.is_active) else None
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
                import json
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
    import sys
    report = {
        "app": {
            "version": settings.VERSION,
            "commit": settings.DEPLOYED_COMMIT or None,
            "deployed_at": settings.DEPLOYED_AT or None,
            "dev_mode": settings.DEV_MODE,
            "python": sys.version.split()[0],
            "uptime_s": int(time.time() - APP_STARTED),
            "pid": __import__("os").getpid(),
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
        "errors": list(_ring.buf)[-150:],
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
    tail = max(1, min(tail, LOG_CAPACITY))
    return PlainTextResponse("\n".join(list(_ring.buf)[-tail:]) or "(no logs yet)")


@router.get("/api/dev/updates")
def dev_updates(request: Request):
    _dev_guard(request)
    return JSONResponse({"ok": True, "updates": _update_history(50)})


@router.get("/dev")
def dev_page(request: Request):
    _dev_guard(request)
    user = _session_user(request)
    return render("dev.html", ctx(request, user, report=collect_report()))
