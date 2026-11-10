"""پل عامل پروژه (v0.4.3) — دسترسی مهارشدهٔ عامل هوش مصنوعیِ کاربر به پروژهٔ خودش.

اینجا نقطهٔ مقابل پل ادمین است: پل ادمین (access-agent مستقل) کل سرور را
می‌بیند؛ این پل فقط «یک پروژه» را. قرارداد امنیتی که همهٔ مسیرهای پل به آن
وابسته‌اند:

1. توکن (`pst_…`) فقط هشِ sha256 در دیتابیس دارد؛ مالکش یک پروژه است
   (AgentToken.project_id) و هیچ مسیری بدون رد شدن از authenticate() نیست.
2. همهٔ استعلام‌های داکری با فیلتر لیبل `parastoo.project=<id>` انجام می‌شود —
   کشف ساختاری، نه اعتماد به نام. کانتینر/مخزن پروژهٔ دیگر از دید پل خارج است.
3. شیم گیت، مخزن مقصد را از دیتابیس می‌سازد (owner/slug پروژهٔ توکن) نه از
   URL کلاینت — مسیر جعلی در URL هیچ اثری ندارد.
4. سقف نرخ به‌ازای هر توکن (پنجرهٔ لغزان در-حافظه) + audit JSONL برای هر فراخوانی.
"""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..db import db_now

TOKEN_PREFIX = "pst_"
MAX_PUSH_BYTES = 200 * 1024 * 1024   # سقف حجم هر push (محافظ حافظهٔ هسته)
EXEC_TIMEOUT_MAX = 120               # ثانیه — سقف هر exec
EXEC_OUTPUT_MAX = 256 * 1024         # سقف بایت خروجی هر exec
LOGS_TAIL_MAX = 2000                 # سقف خطوط لاگ
LOGS_BYTES_MAX = 100 * 1024          # سقف بایت پاسخ لاگ
AUDIT_KEEP = 5000                    # نگه‌داری آخرین رکوردها (هر بار برش)

# سقف نرخ: نام باکت → (حداکثر مجاز، پنجره به ثانیه) — به‌ازای هر توکن
BUCKETS: dict[str, tuple[int, int]] = {
    "status": (120, 60),
    "logs": (60, 60),
    "exec": (20, 60),
    "build": (10, 3600),
    "git": (30, 3600),
    "env": (60, 60),
    "audit": (30, 60),
    "tickets": (30, 60),   # تیکتینگ عضو‌به‌عضو (v0.5.17) — فراخوانی عامل
}

# نام‌گذاری توکن — نمایشی است؛ سخت‌گیرانه تا لاگ‌ها تمیز بماند
_NAME_RE = re.compile(r"^[\w\u0600-\u06FF .()-]{1,80}$", re.UNICODE)


def name_ok(name: str) -> bool:
    return bool(_NAME_RE.match((name or "").strip()))


# ---------- ساخت و اعتبارسنجی توکن ----------

def mint(db, project, name: str, days: int | None = None):
    """ساخت توکن تازه. خروجی: (مقدار خام — فقط یک‌بار نمایش داده می‌شود، ردیف)."""
    from datetime import timedelta

    from ..models import AgentToken
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = None
    if days:
        expires = db_now() + timedelta(days=max(1, min(int(days), 365)))
    row = AgentToken(
        project_id=project.id,
        name=(name or "agent").strip()[:80],
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:12],
        expires_at=expires,
    )
    db.add(row)
    db.commit()
    return raw, row


def authenticate(db, raw: str | None):
    """توکن خام → (ردیف توکن، پروژه) یا None. ابطال/انقضا همین‌جا بسته می‌شود."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    from ..models import AgentToken, Project
    row = (db.query(AgentToken)
           .filter(AgentToken.token_hash == hashlib.sha256(raw.encode()).hexdigest())
           .first())
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at and row.expires_at < db_now():
        return None
    project = db.get(Project, row.project_id)
    if project is None:
        return None
    return row, project


def token_from_headers(request) -> str | None:
    """Bearer از هدر، یا رمزِ Basic auth (شیم گیت: هر نام‌کاربری، رمز = توکن؛
    یا نام‌کاربری = توکن با رمز خالی — سبک https://TOKEN@host/)."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    if h.lower().startswith("basic "):
        import base64
        try:
            user, _, pwd = base64.b64decode(h[6:].strip()).decode("utf-8",
                                                                  errors="replace").partition(":")
        except Exception:
            return None
        if pwd.startswith(TOKEN_PREFIX):
            return pwd
        if user.startswith(TOKEN_PREFIX):
            return user
    return None


# ---------- سقف نرخ (پنجرهٔ لغزان در-حافظه — تک‌فرایندی کافی است) ----------

_windows: dict[tuple[int, str], list[float]] = {}


def allow(token_id: int, bucket: str) -> bool:
    limit, window = BUCKETS.get(bucket, (60, 60))
    now = time.monotonic()
    lst = [t for t in _windows.get((token_id, bucket), []) if now - t < window]
    ok = len(lst) < limit
    if ok:
        lst.append(now)
    _windows[(token_id, bucket)] = lst
    return ok


# ---------- audit ----------

def audit(token, project, action: str, detail: str = "", ip: str = "",
          ok: bool = True) -> None:
    """ثبت best-effort هر فراخوانی پل — DATA_DIR/dev/bridge-audit.jsonl."""
    try:
        d = Path(settings.DATA_DIR) / "dev"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "bridge-audit.jsonl"
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "token": token.prefix if token else "-",
               "project": project.slug if project else "-",
               "action": action, "ok": ok, "ip": ip,
               "detail": (detail or "")[:300]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # برش دوره‌ای — فایل هرگز بی‌کران بزرگ نمی‌شود
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > AUDIT_KEEP:
            p.write_text("\n".join(lines[-AUDIT_KEEP // 2:]) + "\n", encoding="utf-8")
    except Exception:
        pass  # audit هرگز نباید خودش خطا بدهد


def audit_tail(limit: int = 50, token_prefix: str | None = None) -> list[dict]:
    """خواندن آخرین رکوردها — برای گزارش‌گیری عامل (فقط توکن خودش) و ادمین."""
    try:
        p = Path(settings.DATA_DIR) / "dev" / "bridge-audit.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 200)):]
        out = []
        for ln in reversed(lines):
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if token_prefix and rec.get("token") != token_prefix:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ---------- وضعیت پروژه برای گزارش عامل ----------

def _ticket_counts(db, project) -> dict:
    """قلاب اطلاع‌رسانی تیکت (Cycle 2) — صندوق باز پروژه در یک نگاه.

    تک‌منبع شمارش: services.tickets.open_counts — همان تعریف «باز» که
    سقف جفت و نشان UI می‌خوانند. هیچ محتوایی از تیکت اینجا بیرون نمی‌رود
    (تیکت حساس حتی شمارشِ متن را لو نمی‌دهد). عامل با پولینگ همیشگیِ status
    همین را می‌بیند و سراغ `pst tickets` می‌رود.
    """
    from . import tickets as tsvc
    return tsvc.open_counts(db, ("project", project.id))


def project_status(db, project) -> dict:
    """خروجی JSON /api/bridge/status — عامل با همین یک فراخوانی صاحب تصویر می‌شود."""
    from .. import dockerops
    from ..models import Build
    from . import billing

    live = dockerops.project_container_status(project.id)  # خالی = داکر نیست
    services = []
    for s in sorted(project.services, key=lambda x: (not x.is_public, x.name)):
        services.append({
            "name": s.name, "image": s.image, "tier": s.tier,
            "public": s.is_public, "port": s.port,
            "status": s.status, "live": live.get(s.name),
        })
    sites = [{"slug": s.slug, "subdomain": s.subdomain, "status": s.status,
              "url": f"{'https' if _tls() else 'http'}://{s.subdomain}"}
             for s in project.sites]
    latest = (db.query(Build)
              .filter(Build.project_id == project.id)
              .order_by(Build.id.desc()).first())
    running = [s for s in project.services if s.status == "running"]
    return {
        "project": {
            "name": project.name, "slug": project.slug,
            "repo_status": project.repo_status,
            "has_repo": not project.source_project_id,
            "latest_sha": project.latest_sha,
        },
        "services": services,
        "sites": sites,
        "latest_build": ({"id": latest.id, "status": latest.status,
                          "sha": latest.commit_sha[:7],
                          "created_at": latest.created_at.isoformat(sep=" "),
                          "finished_at": (latest.finished_at.isoformat(sep=" ")
                                          if latest.finished_at else None)}
                         if latest else None),
        "hourly_cost": billing.hourly_cost(running),
        "currency": settings.CURRENCY,
        "tickets": _ticket_counts(db, project),
        "version": settings.VERSION,
    }


def _tls() -> bool:
    try:
        from .. import domains
        return domains.tls()
    except Exception:
        return False
