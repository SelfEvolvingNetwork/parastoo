"""صفحهٔ عمومی «راهنمای عامل هوش مصنوعی کاربر» (/docs/agent) — v0.5.3.

تک‌منبعِ همیشه‌به‌روز برای عامل‌هایی که پروژهٔ کاربر را روی پرستو پیش
می‌برند: معماری، دسترسی پل، چرخهٔ توسعه/تست و قواعد پروژهٔ جدید. عمومی
است (بدون نشست) و هیچ رمزی ندارد — توکن فقط به‌صورت جای‌نگهدار. متنِ
آمادهٔ کپیِ عامل در صفحهٔ پروژه است و به همین صفحه ارجاع می‌دهد؛ با هر
استقرار، محتوای این صفحه همان نسخهٔ جاری پنل می‌شود.
"""
from fastapi import APIRouter, Depends, Request

from ..db import get_db
from ..deps import ctx, get_current_user, render

router = APIRouter()


@router.get("/docs/agent")
def agent_docs(request: Request,
               user=Depends(get_current_user),
               db=Depends(get_db)):  # db فقط برای هم‌الگویی وابستگی‌ها؛ استفاده نمی‌شود
    return render("agent_docs.html", ctx(request, user))
