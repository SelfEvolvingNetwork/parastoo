"""پرداخت — چرخهٔ شارژ آنلاین کیف پول با درگاه قابل‌تعویض (فعلاً درگاه آزمایشی).

معماری (اصولی اما حداقلی):
- Payment فقط «درخواست پرداخت» است؛ پول فقط پس از verify درگاه به کیف می‌نشیند
  و همراهش همیشه یک WalletTx در دفتر کل ثبت می‌شود (همان قاعدهٔ پول v0.3.0).
- هر درگاه دو کار دارد: start (آدرسی که مرورگر کاربر برای پرداخت به آن می‌رود)
  و verify (تأیید وضعیت هنگام بازگشت به callback). درگاه واقعی بعداً فقط همین
  دو متد را پیاده می‌کند و به رجیستری _GATEWAYS اضافه می‌شود؛ هیچ‌جای دیگر
  (روت‌ها، قالب‌ها، دفتر کل) تغییر نمی‌کند.
- درگاه فعلی mock است: صفحه‌ای داخل پنل، ولی دقیقاً مثل درگاه واقعی رفتار
  می‌کند — کاربر به درگاه می‌رود، درگاه با GET به /payment/callback برمی‌گرداند
  و سمت پرستو نتیجه تأیید و (idempotent) کیف شارژ می‌شود.
"""
import secrets

from sqlalchemy.orm import Session

from ..config import settings
from ..db import db_now
from ..models import Payment, User, WalletTx


class MockGateway:
    """درگاه آزمایشی (سندباکس) — صفحهٔ داخلی /pay/mock/{token}.

    پرداخت واقعی انجام نمی‌شود؛ هدف این است که مسیر رفت‌وبرگشت، وضعیت‌ها
    (موفق/لغو) و شارژ idempotent کیف، عیناً مثل روزی که درگاه واقعی نشست کار کند.
    """

    key = "mock"
    label = "درگاه آزمایشی (پرداخت واقعی انجام نمی‌شود)"

    def start(self, pay: Payment) -> str:
        return f"/pay/mock/{pay.token}"

    def verify(self, pay: Payment, params: dict) -> tuple[bool, str]:
        """تأیید بازگشت از درگاه: (ok, رسید). mock فقط status=paid را می‌پذیرد."""
        if str(params.get("status", "")) != "paid":
            return False, ""
        return True, f"MOCK-{pay.id:06d}-{secrets.token_hex(4)}"


# رجیستری درگاه‌ها — درگاه واقعی بعداً همین‌جا ثبت می‌شود (تک‌نقطهٔ اتصال)
_GATEWAYS: dict[str, MockGateway] = {"mock": MockGateway()}


def gateways() -> dict[str, dict]:
    """فهرست درگاه‌ها برای UI (select فرم شارژ)."""
    return {key: {"label": impl.label} for key, impl in _GATEWAYS.items()}


def start_charge(db: Session, user: User, amount_raw, gateway_key: str) -> tuple[Payment | None, str]:
    """اعتبارسنجی مبلغ/درگاه + ساخت درخواست پرداخت pending.

    برمی‌گرداند: (payment|None, flash_key) — flash_key == "" یعنی موفق.
    مبلغ از فرم می‌آید؛ فقط عدد صحیح در بازهٔ [MIN_CHARGE, MAX_CHARGE].
    """
    impl = _GATEWAYS.get(str(gateway_key or ""))
    if impl is None:
        return None, "gateway_invalid"
    try:
        amount = int(str(amount_raw).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None, "amount_invalid"
    if amount < settings.MIN_CHARGE or amount > settings.MAX_CHARGE:
        return None, "amount_invalid"
    pay = Payment(user_id=user.id, amount=amount, gateway=impl.key,
                  token=secrets.token_urlsafe(24))
    db.add(pay)
    db.commit()
    return pay, ""


def gateway_url(pay: Payment) -> str:
    """آدرسی که کاربر باید به آن ریدایرکت شود تا پرداخت را انجام دهد."""
    return _GATEWAYS[pay.gateway].start(pay)


def handle_callback(db: Session, user: User, gateway_key: str, token: str, params: dict) -> str:
    """بازگشت از درگاه: تأیید و شارژ کیف، یا ثبت لغو/شکست. flash_key برمی‌گرداند.

    idempotent است: پرداختِ پردازش‌شده دوباره شارژ نمی‌شود (pay_dup).
    """
    impl = _GATEWAYS.get(str(gateway_key or ""))
    pay = db.query(Payment).filter(Payment.token == str(token or "")).first()
    if impl is None or pay is None or pay.user_id != user.id or pay.gateway != impl.key:
        return "not_found"
    if pay.status != "pending":
        return "pay_dup"

    ok, ref = impl.verify(pay, params)
    if not ok:
        canceled = str(params.get("status", "")) == "cancel"
        pay.status = "canceled" if canceled else "failed"
        db.commit()
        return "pay_canceled" if canceled else "pay_failed"

    pay.status = "paid"
    pay.gateway_ref = ref
    pay.paid_at = db_now()
    user.balance += pay.amount
    db.add(WalletTx(user_id=user.id, amount=pay.amount, kind="deposit",
                    note=f"شارژ آنلاین کیف پول — {impl.label} (رسید {ref})"))
    db.commit()
    return "pay_done"
