"""همهٔ روترهای پرستو + ترتیب ثبت در اپ (در main.py پیمایش می‌شود).

ترتیب ثبت فقط برای هم‌پوشانی مسیرها مهم است؛ الان هیچ تداخلی نیست، اما
ترتیب معنایی «ویزارد ← احراز هویت ← صفحات اصلی ← عملیات‌ها» حفظ شده است.
"""
from . import (account, admin, auth, dashboard, dev, health, hooks,
               marketplace, projects, setup, sites, wallet)

ROUTERS = (
    setup.router,        # ویزارد نصب/دامنه
    auth.router,         # ثبت‌نام/ورود/خروج
    account.router,      # حساب کاربری
    dashboard.router,    # / و /dashboard
    health.router,       # healthz و /api/version
    projects.router,
    sites.router,
    marketplace.router,
    wallet.router,
    admin.router,
    hooks.router,        # وب‌هوک gitea
    dev.router,          # حالت توسعه‌دهنده
)
