"""کیف پول: موجودی و تراکنش‌ها + فرم شارژ آنلاین (درگاه آزمایشی از v0.3.4).

شارژ دستیِ ادمین (routes/admin.py credit) همچنان برقرار است؛ شارژ آنلاین
مسیرش در routes/payment.py و منطقش در services/payment.py است.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ctx, csrf_guard, render
from ..models import Site, WalletTx
from ..services import billing as billing_svc
from ..services import payment as payment_svc

router = APIRouter()


@router.get("/wallet")
def wallet(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    txs = (db.query(WalletTx)
           .filter(WalletTx.user_id == user.id)
           .order_by(WalletTx.id.desc())
           .limit(50)
           .all())
    running_sites = (db.query(Site)
                     .filter(Site.owner_id == user.id, Site.status == "running")
                     .all())
    # درس میدانی v0.5.1: hourly_cost سرویس می‌خواهد نه سایت (Site.tier از v0.4.0
    # حذف شده) — پاس‌دادن Siteها یعنی 500 روی کیف پول هر کاربرِ سایتِ روشن.
    # هزینه = سرویس‌های running پروژه‌های سایت‌های روشن، بدون تکرار پروژه.
    seen, running_services = set(), []
    for s in running_sites:
        if s.project_id in seen:
            continue
        seen.add(s.project_id)
        running_services += [x for x in s.project.services if x.status == "running"]
    hourly = billing_svc.hourly_cost(running_services)
    return render("wallet.html", ctx(request, user, txs=txs, hourly=hourly,
                                     pay_gateways=payment_svc.gateways()))
