"""بازار و فروش (v0.5.19): قیمت عمومی، سفارش عمومی، وضعیت عمومی، پنل کسب‌وکار ادمین.

مشورت ۲ مشاور (2026-11-10): دادهٔ کسب‌وکار فقط از فرم ادمین؛ مکانیزم فروش =
/pricing ← /order (سرنخ) ← پیگیری در /admin/business. پرداخت در فاز پایلوت
دستی است (حواله به مشاور) — درگاه واقعی در دورهٔ بی‌مالک قفل §۴.۴ میثاق
است و هیچ مسیری آن را نمی‌خواند. فرم /order عمومی است (بی‌نشست) و مثل
ثبت‌نام CSRF نشستی ندارد؛ ضد-اسپم: honeypot + سقف نرخ سرنخ.
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..db import db_now, get_db
from ..deps import admin_guard, ctx, get_current_user, redirect, render
from ..models import Lead
from ..services import business as biz

router = APIRouter()

LEAD_LIMIT = 8          # سقف سرنخ در پنجرهٔ زمانی — ضد اسپم فرم عمومی
LEAD_WINDOW_MIN = 10


def _lead_rate_ok(db: Session) -> bool:
    cutoff = db_now() - timedelta(minutes=LEAD_WINDOW_MIN)
    return db.query(Lead).filter(Lead.created_at >= cutoff).count() < LEAD_LIMIT


@router.get("/pricing")
def pricing(request: Request, user=Depends(get_current_user),
            db: Session = Depends(get_db)):
    return render("pricing.html", ctx(request, user, items=biz.catalog(db)))


@router.get("/status")
def status_page(request: Request, user=Depends(get_current_user),
                db: Session = Depends(get_db)):
    return render("status.html", ctx(request, user, st=biz.status_summary(db)))


@router.get("/order")
def order_form(request: Request, user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    preselect = request.query_params.get("service", "").strip()
    return render("order.html", ctx(request, user, items=biz.catalog(db),
                                    preselect=preselect))


@router.post("/order")
def order_submit(name: str = Form(""), contact: str = Form(""),
                 service_key: str = Form(""), message: str = Form(""),
                 website: str = Form(""),   # honeypot — بات‌ها پرش می‌کنند
                 db: Session = Depends(get_db)):
    if website.strip():
        return redirect("/order?m=lead_ok")  # بات: ظاهرِ موفق، بدون ذخیرهٔ داده
    name, contact, service_key = name.strip(), contact.strip(), service_key.strip()
    if len(name) < 2 or len(contact) < 5 \
            or service_key not in (s["key"] for s in biz.SERVICES):
        return redirect("/order?m=lead_invalid")
    if not _lead_rate_ok(db):
        return redirect("/order?m=lead_slow")
    db.add(Lead(name=name[:120], contact=contact[:200],
                service_key=service_key, message=message.strip()[:2000]))
    db.commit()
    return redirect("/order?m=lead_ok")


@router.get("/admin/business")
def admin_business(request: Request, user=Depends(admin_guard),
                   db: Session = Depends(get_db)):
    leads = db.query(Lead).order_by(Lead.id.desc()).limit(100).all()
    return render("business.html", ctx(request, user, items=biz.catalog(db),
                                       fields=biz.BUSINESS_FIELDS,
                                       params={f["key"]: biz.get_param(db, f["key"])
                                               for f in biz.BUSINESS_FIELDS},
                                       leads=leads))


@router.post("/admin/business/params")
def admin_params(request: Request,
                 server_cost_monthly: str = Form(""),
                 server_due_date: str = Form(""),
                 domain_due_date: str = Form(""),
                 advisor_salary_monthly: str = Form(""),
                 user=Depends(admin_guard), db: Session = Depends(get_db)):
    values = {"server_cost_monthly": server_cost_monthly,
              "server_due_date": server_due_date,
              "domain_due_date": domain_due_date,
              "advisor_salary_monthly": advisor_salary_monthly}
    for field in biz.BUSINESS_FIELDS:
        v = values[field["key"]].strip()
        if field["kind"] == "int" and v and not v.isdigit():
            return redirect("/admin/business?m=biz_invalid")
        if v:
            biz.set_param(db, field["key"], v)
    db.commit()
    return redirect("/admin/business?m=biz_saved")


@router.post("/admin/business/prices")
def admin_prices(request: Request,
                 price_site_basic: str = Form(""),
                 price_site_domain: str = Form(""),
                 price_dev_git: str = Form(""),
                 price_dev_agent: str = Form(""),
                 price_managed_sla: str = Form(""),
                 user=Depends(admin_guard), db: Session = Depends(get_db)):
    values = {"site-basic": price_site_basic, "site-domain": price_site_domain,
              "dev-git": price_dev_git, "dev-agent": price_dev_agent,
              "managed-sla": price_managed_sla}
    for key, raw in values.items():
        if raw.strip() and not raw.strip().isdigit():
            return redirect("/admin/business?m=biz_invalid")
        biz.set_price(db, key, raw)
    db.commit()
    return redirect("/admin/business?m=biz_saved")


@router.post("/admin/business/lead/{lead_id}/status")
def admin_lead_status(request: Request, lead_id: int, status: str = Form(...),
                      user=Depends(admin_guard), db: Session = Depends(get_db)):
    if status not in biz.LEAD_STATUSES:
        return redirect("/admin/business?m=biz_invalid")
    lead = db.get(Lead, lead_id)
    if lead is None:
        return redirect("/admin/business?m=not_found")
    lead.status = status
    db.commit()
    return redirect("/admin/business?m=biz_saved")
