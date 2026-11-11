"""سرویس ایمیل خروجی — تک‌منبع نامه‌های پنل (v0.5.23).

دو پشتیبان:
- SMTP خارجی: وقتی PARASTOO_SMTP_HOST در env هست (پورت 587 + STARTTLS
  پیش‌فرض، یا 465 با PARASTOO_SMTP_SSL=true؛ احراز فقط اگر کاربر/رمز داده
  شده باشد).
- صندوق فایل (فالبک): هاست خالی → نامه به DATA_DIR/mail/outbox.jsonl ثبت
  می‌شود تا جریان یادآوری رمز بدون SMTP هم کامل و آزمایش‌پذیر باشد؛ مالک
  بعداً فقط با env، SMTP واقعی را وصل می‌کند — هیچ تغییری در کد لازم نیست.

قراردادها (وصیت ۹ — امنیت=ریل‌گذاری):
- ارسال هرگز مسیر HTTP را نمی‌شکند: خطا لاگ می‌شود، نه raise (best-effort).
- هیچ رمز/توکنی در لاگ نمی‌آید — فقط نوع خطا.
- متن‌نامه همیشه متن سادهٔ UTF-8 است (بدون HTML — همان سیاست تیکت).
"""
import json
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from ..config import settings

log = logging.getLogger("parastoo.email")


def backend() -> str:
    """نام پشتیبان فعال — «smtp» یا «file»."""
    return "smtp" if settings.SMTP_HOST else "file"


def outbox_path() -> Path:
    return Path(settings.DATA_DIR) / "mail" / "outbox.jsonl"


def _from_header() -> str:
    addr = settings.SMTP_FROM or f"no-reply@{settings.BASE_DOMAIN}"
    name = settings.SMTP_FROM_NAME.strip()
    return f"{name} <{addr}>" if name else addr


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from_header()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")
    return msg


def send_email(to: str, subject: str, body: str) -> dict:
    """ارسال نامهٔ متن‌ساده؛ خروجی {"ok": bool, "backend": str, "error"?}.

    هیچ‌گاه exception بالا نمی‌دهد — نامهٔ نرفته هرگز فرمِ حساب را نمی‌شکند.
    """
    to = (to or "").strip()
    if not to:
        return {"ok": False, "backend": backend(), "error": "empty_to"}
    if backend() == "smtp":
        return _send_smtp(to, subject, body)
    return _send_file(to, subject, body)


def _send_smtp(to: str, subject: str, body: str) -> dict:
    try:
        msg = _build_message(to, subject, body)
        if settings.SMTP_SSL:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                  timeout=15) as s:
                _auth(s)
                s.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=15) as s:
                s.ehlo()
                if settings.SMTP_PORT == 587 or s.has_extn("starttls"):
                    s.starttls()
                    s.ehlo()
                _auth(s)
                s.send_message(msg)
        return {"ok": True, "backend": "smtp"}
    except Exception as exc:
        # فقط نوع خطا — محتوای پیام و اعتبار هرگز لاگ نمی‌شوند
        log.warning("smtp send failed: %s", type(exc).__name__)
        return {"ok": False, "backend": "smtp", "error": type(exc).__name__}


def _auth(s: smtplib.SMTP) -> None:
    if settings.SMTP_USER and settings.SMTP_PASS:
        s.login(settings.SMTP_USER, settings.SMTP_PASS)


def _send_file(to: str, subject: str, body: str) -> dict:
    try:
        p = outbox_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "to": to,
            "subject": subject,
            "body": body,
            "backend": "file",
        }
        first = not p.exists()
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if first:
            try:
                os.chmod(p, 0o600)  # ممکن است لینک بازنشانی داخلش باشد
            except Exception:
                pass
        return {"ok": True, "backend": "file"}
    except Exception as exc:
        log.warning("file outbox write failed: %s", type(exc).__name__)
        return {"ok": False, "backend": "file", "error": type(exc).__name__}
