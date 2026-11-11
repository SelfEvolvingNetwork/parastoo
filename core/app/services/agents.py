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


# ---------- بوت‌استرپ خودکار توکن agent:1 (فرمان مالک ۱۱-۱۱ ششم) ----------
# هیچ‌گاه از کاربر/ادمین توکن pat_ نخواهیم. عامل پرستو خودش توکن خودش را
# می‌سازد و نگه می‌دارد. این تابع در startup صدا زده می‌شود: اگر agent:1
# توکن فعال ندارد، یکی می‌سازد و مقدار خام را در فایل ذخیره می‌کند.
# اگر فایل نباشد (مثلاً پس از ری‌استارت کانتینر روی volume تازه)، یکی تازه
# می‌سازد و توکن کهنه را ابطال می‌کند — هیچ‌گاه «بدون توکن» نمی‌مانیم.
#
# مسیر فایل از config خوانده می‌شود (AGENT_TICKET_TOKEN_FILE). فایل فقط‌خواندنی
# برای پروسهٔ parastoo-core نیست — خود هسته آن را می‌نویسد؛ عامل پرستو (از
# بیرون کانتینر) با docker exec یا exec سرور آن را می‌خواند.

def ensure_default_agent_token(db) -> str | None:
    """اگر agent:1 توکن فعال pat_ ندارد، یکی بساز و در فایل بنویس.

    خروجی: مقدار خام توکن (یا None اگر ناموفق بود). idempotent: اگر توکن
    فعال هست و فایل هم هست، کاری نمی‌کند.
    """
    from ..models import AgentPatToken
    agent = ensure_default_agent(db)
    if agent is None:
        return None

    token_file = _agent_token_file()
    active = (db.query(AgentPatToken)
              .filter(AgentPatToken.agent_id == agent.id,
                      AgentPatToken.revoked_at.is_(None))
              .order_by(AgentPatToken.id.desc()).first())

    # اگر توکن فعال هست و فایل هم هست و قابل خواندن است → کاری نکن
    if active is not None and _token_file_valid(token_file):
        return None

    # اگر توکن فعال هست ولی فایل نیست (یعنی کانتینر ری‌استارت شده روی volume
    # تازه، یا فایل دستی پاک شده) → توکن کهنه را ابطال و یکی تازه بساز.
    # (ما مقدار خام توکن کهنه را نداریم — فقط هش داریم؛ پس نمی‌توانیم فایل
    # را بازسازی کنیم. یک توکن تازه امن‌ترین راه است.)
    if active is not None and not _token_file_valid(token_file):
        try:
            from ..db import db_now
            active.revoked_at = db_now()
            active.name = (active.name or "agent") + " [auto-revoked: file lost]"
            db.commit()
        except Exception:
            db.rollback()

    raw, _row = mint(db, agent, "بوت‌استرپ خودکار عامل پرستو")
    if not _write_token_file(token_file, raw):
        # فایل نوشته نشد (مثلاً docker read-only) — توکن ساخته شد ولی عامل
        # نمی‌تواند آن را بخواند. در audit ثبت می‌شود؛ ادمین می‌تواند ببیند.
        return None
    return raw


def _agent_token_file() -> str:
    """مسیر فایل توکن عامل — از config یا پیش‌فرض DATA_DIR/agent-ticket.env."""
    return getattr(settings, "AGENT_TICKET_TOKEN_FILE", "") \
        or str(Path(settings.DATA_DIR) / "agent-ticket.env")


def _token_file_valid(path: str) -> bool:
    """آیا فایل توکن هست و مقدار pat_ دارد؟"""
    try:
        p = Path(path)
        if not p.is_file():
            return False
        text = p.read_text(encoding="utf-8")
        # یک خط کلید=مقدار با پیشوند pat_
        for ln in text.splitlines():
            ln = ln.strip()
            if ln.startswith("#") or "=" not in ln:
                continue
            val = ln.split("=", 1)[1].strip().strip('"').strip("'")
            if val.startswith(TOKEN_PREFIX):
                return True
        return False
    except Exception:
        return False


def _write_token_file(path: str, raw: str) -> bool:
    """نوشتن فایل توکن با chmod 600. best-effort — خطا = False."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # فقط مقدار خام — کلید ثابت برای خوانندهٔ عامل
        p.write_text(f'AGENT_TICKET_TOKEN="{raw}"\n', encoding="utf-8")
        try:
            p.chmod(0o600)
        except Exception:
            pass  # chmod در برخی filesystemها (مثل volume داکر) ممکن است نرود
        return True
    except Exception:
        return False



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
