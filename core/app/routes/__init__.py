"""همهٔ روترهای پرستو + ترتیب ثبت در اپ (در main.py پیمایش می‌شود).

ترتیب ثبت فقط برای هم‌پوشانی مسیرها مهم است؛ الان هیچ تداخلی نیست، اما
ترتیب معنایی «ویزارد ← احراز هویت ← صفحات اصلی ← عملیات‌ها» حفظ شده است.
"""
from . import (account, admin, agentdocs, apidocs, auth, bridge, business, dashboard,
               dev, dns, downloads, gitsso, health, hooks, marketplace,
               oauthidp, payment, projects, setup, sites, tickets, wallet)

ROUTERS = (
    setup.router,        # ویزارد نصب/دامنه
    auth.router,         # ثبت‌نام/ورود/خروج
    account.router,      # حساب کاربری
    oauthidp.router,     # /oauth/* — تأمین‌کنندهٔ OAuth2 پنل (SSO اصولی v0.5.7)
    gitsso.router,       # /git/sso — در ورود به جریان OAuth2 گیتا (v0.5.7)
    dashboard.router,    # / و /dashboard
    health.router,       # healthz و /api/version
    apidocs.router,      # /api/docs — پاکت هم‌شکل خانواده (مرجع فنی خودمعرف، v0.5.30)
    agentdocs.router,    # /docs/agent — راهنمای عمومی عامل هوش مصنوعی کاربر
    downloads.router,    # /download — انتشار خودکار اپ ادمین پرستو موبایل (v0.5.9)
    business.router,     # بازار و فروش (v0.5.19): /pricing /order /status + /admin/business
    projects.router,
    sites.router,
    marketplace.router,
    payment.router,      # پرداخت آنلاین کیف پول (درگاه آزمایشی)
    wallet.router,
    admin.router,
    dns.router,          # نیم‌سرور اختصاصی (UI ادمین + API)
    hooks.router,        # وب‌هوک gitea
    bridge.router,       # پل عامل پروژه (v0.4.3): /api/bridge/* + شیم گیت
    tickets.router,      # تیکتینگ عضو‌به‌عضو (v0.5.17): /api/tickets*
    dev.router,          # کنسول توسعه (همیشه روشن؛ ادمین یا X-Dev-Token)
)
