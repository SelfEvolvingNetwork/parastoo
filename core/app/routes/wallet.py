"""کیف پول: موجودی و تراکنش‌ها. شارژ در MVP توسط ادمین انجام می‌شود."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..deps import ctx, csrf_guard, redirect, render
from ..db import get_db
from ..models import Site, WalletTx

router = APIRouter()


@router.get("/wallet")
def wallet(request: Request, user=Depends(csrf_guard), db: Session = Depends(get_db)):
    txs = (db.query(WalletTx)
           .filter(WalletTx.user_id == user.id)
           .order_by(WalletTx.id.desc())
           .limit(50)
           .all())
    running = (db.query(Site)
               .filter(Site.owner_id == user.id, Site.status == "running")
               .all())
    hourly = sum(settings.TIERS.get(s.tier, settings.TIERS["nano"])["price"] for s in running)
    return render("wallet.html", ctx(request, user, txs=txs, hourly=hourly))


@router.post("/wallet/request")
def wallet_request(user=Depends(csrf_guard)):
    # MVP: درخواست شارژ ثبت نمی‌شود؛ راهنما در صفحه کیف پول هست
    return redirect("/wallet?m=error")
