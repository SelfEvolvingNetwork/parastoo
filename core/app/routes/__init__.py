"""همهٔ روترهای پرستو + ترتیب ثبت در اپ (در main.py پیمایش می‌شود).

ترتیب ثبت فقط برای هم‌پوشانی مسیرها مهم است؛ الان هیچ تداخلی نیست، اما
ترتیب معنایی «ویزارد ← احراز هویت ← صفحات اصلی ← عملیات‌ها» حفظ شده است.
"""
from . import (account, admin, auth, bridge, dashboard, dev, dns, health,
               hooks, marketplace, payment, projects, setup, sites, wallet)

ROUTERS = (
    setup.router,        # ویزارد نصب/دامنه
    auth.router,         # ثبت‌نام/ورود/خروج
    account.router,      # حساب کاربری
    dashboard.router,    # / و /dashboard
    health.router,       # healthz و /api/version
    projects.router,
    sites.router,
    marketplace.router,
    payment.router,      # پرداخت آنلاین کیف پول (درگاه آزمایشی)
    wallet.router,
    admin.router,
    dns.router,          # نیم‌سرور اختصاصی (UI ادمین + API)
    hooks.router,        # وب‌هوک gitea
    bridge.router,       # پل عامل پروژه (v0.4.3): /api/bridge/* + شیم گیت
    dev.router,          # حالت توسعه‌دهنده
)
