"""منطق یادآوری رمز عبور — تک‌منبع (v0.5.23؛ routes/auth فقط نازک صدا می‌زند).

قراردادها:
- توکن خام فقط در نامه می‌آید؛ در دیتابیس فقط sha256 آن (مدل PasswordResetToken).
- یک‌بارمصرف، ۶۰ دقیقه اعتبار.
- ضد-طوفان: هر کاربر حداقل ۱۲۰ ثانیه فاصله بین دو درخواست و حداکثر ۳ درخواست
  در ساعت — پاسخ HTTP در همهٔ حالت‌ها یکسان است (وجود حساب و محدودیت لو نمی‌رود).
- رکوردهای تمام‌شدهٔ قدیمی در هر درخواست جدید پاک می‌شوند (خودپاک‌ساز، بدون
  ورکر جدید — همان الگوی سادگی تیکتینگ).
"""
import hashlib
import secrets
from datetime import timedelta

from ..db import db_now
from ..models import PasswordResetToken, User

TOKEN_TTL_S = 3600        # اعتبار لینک بازنشانی
ISSUE_COOLDOWN_S = 120    # حداقل فاصلهٔ دو درخواست یک کاربر
MAX_PER_HOUR = 3          # سقف درخواست در ساعت برای یک کاربر
GC_AFTER_S = 86400        # رکورد تمام‌شده تا ۲۴ ساعت بعد برای ممیزی می‌ماند


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _gc(db) -> None:
    cutoff = db_now() - timedelta(seconds=GC_AFTER_S + TOKEN_TTL_S)
    db.query(PasswordResetToken).filter(
        (PasswordResetToken.used == True)  # noqa: E712
        | (PasswordResetToken.expires_at < cutoff)
    ).delete(synchronize_session=False)


def issue(db, user: User, ip: str = "") -> str | None:
    """توکن تازه می‌سازد و مقدار خام را برمی‌گرداند؛ محدودیت نرخ → None."""
    _gc(db)
    now = db_now()
    recent = (db.query(PasswordResetToken)
              .filter(PasswordResetToken.user_id == user.id,
                      PasswordResetToken.created_at
                      > now - timedelta(seconds=3600))
              .order_by(PasswordResetToken.created_at.desc()).all())
    if len(recent) >= MAX_PER_HOUR:
        return None
    if recent and (now - recent[0].created_at).total_seconds() < ISSUE_COOLDOWN_S:
        return None
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=_hash(raw),
        expires_at=now + timedelta(seconds=TOKEN_TTL_S),
        request_ip=(ip or "")[:64],
    ))
    db.commit()
    return raw


def _valid_row(db, raw: str) -> PasswordResetToken | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    row = (db.query(PasswordResetToken)
           .filter(PasswordResetToken.token_hash == _hash(raw)).first())
    if row is None or row.used:
        return None
    now = db_now()
    if row.expires_at < now or (now - row.created_at).total_seconds() > TOKEN_TTL_S:
        return None
    return row


def peek(db, raw: str) -> bool:
    """توکن قابل‌مصرف هست؟ (برای رندر فرم — مصرف نمی‌کند)"""
    return _valid_row(db, raw) is not None


def consume(db, raw: str) -> User | None:
    """توکن معتبر → مصرفش می‌کند و کاربرش را می‌دهد؛ وگرنه None."""
    row = _valid_row(db, raw)
    if row is None:
        return None
    row.used = True
    db.commit()
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user
