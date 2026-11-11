"""عضو درجه‌یک «agent» — احراز و امکانات عامل اکوسیستم پرستو (pat_).

Cycle 5 (فرمان مالک ۲۰۲۶-۱۱-۱۱): کانال درخواست به عامل = تیکت. عامل
اکوسیستم با توکن pat_ (هدر Bearer) همان مسیرهای /api/tickets* را
می‌خواند که انسان و عامل پروژه — هیچ مسیر موازی ساخته نمی‌شود.

همان قرارداد امنیتی پل عامل پروژه (services/bridge.py):
1. مقدار خام فقط یک‌بار نمایش داده می‌شود؛ دیتابیس فقط sha256 دارد.
2. سقف نرخ به‌ازای هر توکن (پنجرهٔ لغزان در-حافظه) — تک‌فرایندی کافی است.
3. audit JSONL هر فراخوانی (dev/agent-audit.jsonl — از پل جدا).
4. قلمرو مستقل: ابطال pat_ توکن پل (pst_) و ادمین را نمی‌شکند، و برعکس.
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

TOKEN_PREFIX = "pat_"
DEFAULT_AGENT_ID = 1
DEFAULT_AGENT_NAME = "پرستو"
DEFAULT_AGENT_NOTE = "مرکز فرماندهی پرستو — عامل اکوسیستم"

# سقف نرخ: نام باکت → (حداکثر مجاز، پنجره به ثانیه) — به‌ازای هر توکن
BUCKETS: dict[str, tuple[int, int]] = {
    "tickets": (60, 60),   # همهٔ فراخوانی‌های تیکتینگ عامل اکوسیستم
}

AUDIT_KEEP = 5000   # نگه‌داری آخرین رکوردها (هر بار برش — همان الگوی پل)

# نام‌گذاری توکن — نمایشی است؛ سخت‌گیرانه تا لاگ‌ها تمیز بمانند
_NAME_RE = re.compile(r"^[\w\u0600-\u06FF .()-]{1,80}$", re.UNICODE)


def name_ok(name: str) -> bool:
    return bool(_NAME_RE.match((name or "").strip()))


def ensure_default_agent(db):
    """عضو id=1 «پرستو» — ساخته‌شده به‌صورت تنبل با نخستین استفاده.

    خواندنِ هم‌زمان چند فرایند بدترین حالتش «رَدیف تکراری با id صریح» است
    که دیتابیس رد می‌کند — فراخواننده فقط ردیف را برمی‌گرداند؛ فرایندِ
    بازنده خطای یکتایی را می‌بلعد و دوباره می‌خواند.
    """
    from ..models import EcosystemAgent
    row = db.get(EcosystemAgent, DEFAULT_AGENT_ID)
    if row is None:
        try:
            row = EcosystemAgent(id=DEFAULT_AGENT_ID, name=DEFAULT_AGENT_NAME,
                                 note=DEFAULT_AGENT_NOTE)
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            row = db.get(EcosystemAgent, DEFAULT_AGENT_ID)
    return row


def mint(db, agent, name: str, days: int | None = None):
    """ساخت توکن تازه. خروجی: (مقدار خام — فقط یک‌بار نمایش، ردیف)."""
    from datetime import timedelta

    from ..models import AgentPatToken
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = None
    if days:
        expires = db_now() + timedelta(days=max(1, min(int(days), 365)))
    row = AgentPatToken(
        agent_id=agent.id,
        name=(name or "agent").strip()[:80],
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        prefix=raw[:12],
        expires_at=expires,
    )
    db.add(row)
    db.commit()
    return raw, row


def authenticate(db, raw: str | None):
    """توکن خام pat_ → (ردیف توکن، عضو agent) یا None.
    ابطال/انقضا/غیرفعال‌بودن عضو همین‌جا بسته می‌شود."""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    from ..models import AgentPatToken, EcosystemAgent
    row = (db.query(AgentPatToken)
           .filter(AgentPatToken.token_hash == hashlib.sha256(raw.encode()).hexdigest())
           .first())
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at and row.expires_at < db_now():
        return None
    agent = db.get(EcosystemAgent, row.agent_id)
    if agent is None or not agent.is_active:
        return None
    try:   # best-effort — نشاندن آخرین استفاده هرگز احراز را نمی‌شکند
        row.last_used_at = db_now()
        db.commit()
    except Exception:
        db.rollback()
    return row, agent


# ---------- سقف نرخ (پنجرهٔ لغزان در-حافظه — همان الگوی پل) ----------

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

def audit(token, agent, action: str, detail: str = "", ip: str = "",
          ok: bool = True) -> None:
    """ثبت best-effort هر فراخوانی عامل اکوسیستم — DATA_DIR/dev/agent-audit.jsonl."""
    try:
        d = Path(settings.DATA_DIR) / "dev"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agent-audit.jsonl"
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "token": token.prefix if token else "-",
               "agent": agent.name if agent else "-",
               "action": action, "ok": ok, "ip": ip,
               "detail": (detail or "")[:300]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > AUDIT_KEEP:
            p.write_text("\n".join(lines[-AUDIT_KEEP // 2:]) + "\n", encoding="utf-8")
    except Exception:
        pass  # audit هرگز نباید خودش خطا بدهد
