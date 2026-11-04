"""پرداخت آنلاین کیف پول — درگاه آزمایشی (mock) با معماری قابل‌تعویض.

جریان عیناً مثل درگاه واقعی است تا بعداً جای‌گزینی درگاه، روت‌ها را عوض نکند:
1) POST /wallet/charge (فرم کیف پول) → ساخت درخواست pending → ریدایرکت به درگاه
2) کاربر در صفحهٔ درگاه پرداخت را انجام می‌دهد یا لغو می‌کند
3) درگاه مرورگر را با GET به /payment/callback/{gateway} برمی‌گرداند
4) callback وضعیت را از سرویس پرداخت تأیید می‌کند → کیف شارژ + دفتر کل
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ctx, csrf_guard, redirect, render, require_login
from ..models import Payment, User
from ..services import payment as payment_svc

router = APIRouter()


def _owned_pending_payment(db: Session, user: User, token: str) -> Payment | None:
    """درخواست پرداختِ متعلق به کاربر جاری — وگرنه None (مسیر 404 می‌دهد)."""
    pay = db.query(Payment).filter(Payment.token == str(token or "")).first()
    if pay is None or pay.user_id != user.id:
        return None
    return pay


@router.post("/wallet/charge")
async def wallet_charge(request: Request, user: User = Depends(csrf_guard),
                        db: Session = Depends(get_db)):
    """شروع شارژ: اعتبارسنجی مبلغ → درخواست pending → ریدایرکت به درگاه."""
    form = await request.form()
    pay, err = payment_svc.start_charge(db, user, form.get("amount", ""),
                                        str(form.get("gateway", "")))
    if err:
        return redirect("/wallet", m=err)
    return redirect(payment_svc.gateway_url(pay))


@router.get("/pay/mock/{token}")
def mock_page(token: str, request: Request, user: User = Depends(require_login),
              db: Session = Depends(get_db)):
    """صفحهٔ درگاه آزمایشی — فقط برای مالکِ درخواستِ در انتظار."""
    pay = _owned_pending_payment(db, user, token)
    if pay is None:
        raise HTTPException(status_code=404)
    if pay.gateway != "mock":
        raise HTTPException(status_code=404)
    if pay.status != "pending":
        return redirect("/wallet", m="pay_dup")
    return render("pay_mock.html", ctx(request, user, pay=pay))


@router.post("/pay/mock/{token}/result")
async def mock_result(token: str, request: Request, user: User = Depends(csrf_guard),
                      db: Session = Depends(get_db)):
    """نتیجهٔ پرداخت در درگاه mock — درگاه واقعی همین‌جا پردازش می‌کند و بعد
    مرورگر را به callback برمی‌گرداند؛ ما هم دقیقاً همان کار را می‌کنیم."""
    pay = _owned_pending_payment(db, user, token)
    if pay is None or pay.gateway != "mock":
        raise HTTPException(status_code=404)
    if pay.status != "pending":
        return redirect("/wallet", m="pay_dup")
    form = await request.form()
    result = "paid" if str(form.get("result", "")) == "paid" else "cancel"
    return redirect(f"/payment/callback/mock?token={pay.token}&status={result}")


@router.get("/payment/callback/{gateway}")
def payment_callback(gateway: str, request: Request, token: str = "",
                     user: User = Depends(require_login),
                     db: Session = Depends(get_db)):
    """بازگشت از درگاه (GET مثل همهٔ درگاه‌های واقعی): تأیید و شارژ کیف."""
    flash_key = payment_svc.handle_callback(db, user, gateway, token,
                                            dict(request.query_params))
    return redirect("/wallet", m=flash_key)
