"""پیکربندی پرستو — همه مقادیر از متغیرهای محیطی خوانده می‌شوند (توسط install.sh در .env نوشته می‌شود)."""
import os


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- پایه ---
    APP_NAME: str = "پرستو"
    VERSION: str = "0.5.29"
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")

    # --- دیتابیس ---
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", "sqlite:////tmp/parastoo-dev.db"
    )

    # --- دامنه‌ها ---
    BASE_DOMAIN: str = os.environ.get("BASE_DOMAIN", "127.0.0.1.nip.io")
    PANEL_DOMAIN: str = os.environ.get("PANEL_DOMAIN", f"panel.{BASE_DOMAIN}")
    GIT_DOMAIN: str = os.environ.get("GIT_DOMAIN", f"git.{BASE_DOMAIN}")
    APPS_DOMAIN: str = os.environ.get("APPS_DOMAIN", f"apps.{BASE_DOMAIN}")
    TLS_ENABLED: bool = _bool("TLS_ENABLED")
    # ایمیل ACME — فقط برای همگام‌ساز پیکربندی (v0.3.33): وقتی ویزارد هنوز مقدار
    # دیتابیسی ندارد، رندر باید همان چیزی باشد که install.sh از env ساخت
    ACME_EMAIL: str = os.environ.get("ACME_EMAIL", "").strip()

    # --- CA گواهی (v0.3.33) — همان‌ها که install.sh در .env می‌نویسد؛
    # بازنویسی استاتیک ترافیک در ویزارد دیگر EAB زروسsl را بی‌صدا نمی‌بُرد
    ACME_CA: str = os.environ.get("PARASTOO_ACME_CA", "").strip().lower()
    ACME_EAB_KID: str = os.environ.get("PARASTOO_ACME_EAB_KID", "").strip()
    ACME_EAB_HMAC: str = os.environ.get("PARASTOO_ACME_EAB_HMAC", "").strip()

    # --- ویزارد نصب (v0.2) ---
    # توکن یک‌بارمصرف نصب — فقط در خروجی install.sh چاپ می‌شود و گیتِ ویزارد وب است
    SETUP_TOKEN: str = os.environ.get("PARASTOO_SETUP_TOKEN", "")
    # IP عمومی سرور (install.sh تشخیص می‌دهد) — برای بررسی رکوردهای DNS در ویزارد
    PUBLIC_IP: str = os.environ.get("PARASTOO_PUBLIC_IP", "")
    # پوشه پیکربندی ترافیک روی هاست که داخل کانتینر سوار شده — ویزارد دامنه آن را بازنویسی می‌کند
    CONFIG_DIR: str = os.environ.get("PARASTOO_CONFIG_DIR", "/app/host-config")
    TRAEFIK_CONTAINER: str = os.environ.get("PARASTOO_TRAEFIK_CONTAINER", "parastoo-traefik")
    DNS_CONTAINER: str = os.environ.get("PARASTOO_DNS_CONTAINER", "parastoo-dns")
    # (v0.3.22) .env هاست (سوار در /app/host-env/.env): تغییر دامنه، GITEA_ROOT_URL و
    # دامنه‌ها را اینجا همگام می‌کند تا اجرای بعدی docker compose مقدار کهنه را پس نزند.
    # خالی = غیرفعال (محیط توسعه).
    HOST_ENV_FILE: str = os.environ.get("PARASTOO_HOST_ENV_FILE", "/app/host-env/.env")

    # --- استقرار و آپدیت خودکار (auto-update.sh روی سرور می‌نویسد؛ در .env) ---
    # کامیت مستقر روی این سرور و زمان استقرار — توسط install.sh/auto-update.sh ثبت می‌شود
    DEPLOYED_COMMIT: str = os.environ.get("PARASTOO_DEPLOYED_COMMIT", "").strip()
    DEPLOYED_AT: str = os.environ.get("PARASTOO_DEPLOYED_AT", "").strip()
    # --- کنسول توسعه (v0.5.29 — فرمان مالک ۲۰۲۶-۱۱-۱۲: حذف مفهوم دو-حالتی) ---
    # معماری واحد است: تجاریِ همواره در حال توسعه مستمر — ابزارهای /dev و
    # /api/dev/* همیشه روشن‌اند و پشت احراز هستند (ادمین یا X-Dev-Token).
    # توکن اختیاری برای دسترسی curl/عامل بدون لاگین: PARASTOO_DEV_TOKEN=xxx ← هدر X-Dev-Token: xxx
    DEV_TOKEN: str = os.environ.get("PARASTOO_DEV_TOKEN", "").strip()

    # --- Gitea ---
    GITEA_INTERNAL_URL: str = os.environ.get("GITEA_INTERNAL_URL", "http://gitea:3000")
    REGISTRY_INTERNAL: str = os.environ.get("REGISTRY_INTERNAL", "127.0.0.1:3000")
    GITEA_CONTAINER: str = os.environ.get("GITEA_CONTAINER", "parastoo-gitea")
    GITEA_ADMIN_USER: str = os.environ.get("GITEA_ADMIN_USER", "parastoo-system")
    GITEA_ADMIN_EMAIL: str = os.environ.get("GITEA_ADMIN_EMAIL", "system@parastoo.local")
    GITEA_ADMIN_PASSWORD: str = os.environ.get("GITEA_ADMIN_PASSWORD", "")

    # --- داکر / مسیرها ---
    DOCKER_SOCK: str = os.environ.get("DOCKER_SOCK", "unix:///var/run/docker.sock")
    APPS_NETWORK: str = os.environ.get("APPS_NETWORK", "parastoo-apps")
    DATA_DIR: str = os.environ.get("DATA_DIR", "/data")

    # --- بوت‌استرپ خودکار توکن عامل اکوسیستم (فرمان مالک ۱۱-۱۱ ششم) ---
    # عامل پرستو (agent:1) توکن pat_ خودش را خودکار می‌سازد — هیچ‌گاه از کاربر/ادمین
    # توکن نخواسته نمی‌شود. مقدار خام در این فایل ذخیره می‌شود (chmod 600).
    # خالی = پیش‌فرض DATA_DIR/agent-ticket.env. فایل در volume داکر است که هسته
    # می‌نویسد و عامل پرستو (از بیرون کانتینر) با docker exec می‌خواند.
    AGENT_TICKET_TOKEN_FILE: str = os.environ.get(
        "PARASTOO_AGENT_TICKET_TOKEN_FILE", "").strip()

    # --- ایمیل خروجی (v0.5.23): نامه‌های حساب — یادآوری رمز، خوش‌آمد، اطلاع تغییر رمز ---
    # SMTP خارجی پیشنهادی است؛ PARASTOO_SMTP_HOST خالی = پشتیبان فایل:
    # نامه‌ها در DATA_DIR/mail/outbox.jsonl ثبت می‌شوند تا جریان یادآوری رمز
    # بدون SMTP هم کامل و آزمایش‌پذیر باشد (اتصال SMTP فقط با env — قرارداد §M2).
    SMTP_HOST: str = os.environ.get("PARASTOO_SMTP_HOST", "").strip()
    SMTP_PORT: int = int(os.environ.get("PARASTOO_SMTP_PORT", "587"))
    SMTP_USER: str = os.environ.get("PARASTOO_SMTP_USER", "").strip()
    SMTP_PASS: str = os.environ.get("PARASTOO_SMTP_PASS", "").strip()
    SMTP_SSL: bool = _bool("PARASTOO_SMTP_SSL")  # true = SMTPS پورت 465
    SMTP_FROM: str = os.environ.get("PARASTOO_SMTP_FROM", "").strip()  # خالی = no-reply@BASE_DOMAIN
    SMTP_FROM_NAME: str = os.environ.get("PARASTOO_SMTP_FROM_NAME", "پرستو")

    # --- رفتار ---
    REGISTER_OPEN: bool = _bool("REGISTER_OPEN", "true")
    DISABLE_WORKERS: bool = _bool("PARASTOO_DISABLE_WORKERS")
    TIMEZONE: str = os.environ.get("PARASTOO_TZ", "Asia/Tehran")

    # --- تحریم‌ها: آینهٔ pip/npm برای بیلدهای قالبی کاربران (خالی = پیش‌فرض رسمی) ---
    # در install.sh/.env ست می‌شود؛ stack.py موقع تولید داکرفایل، آنها را داخل فایل می‌نویسد
    PIP_INDEX_URL: str = os.environ.get("PARASTOO_PIP_INDEX_URL", "").strip()
    NPM_REGISTRY: str = os.environ.get("PARASTOO_NPM_REGISTRY", "").strip()

    # --- کیف پول و منابع ---
    CURRENCY: str = "تومان"
    # سقف بدهی؛ وقتی موجودی از این منفی‌تر شود سایت متوقف می‌شود
    DEBT_LIMIT: int = int(os.environ.get("PARASTOO_DEBT_LIMIT", "0"))
    # کمیسیون پرستو از فروش ایمیج در مارکت‌پلیس (درصد)
    COMMISSION_PERCENT: int = int(os.environ.get("PARASTOO_COMMISSION", "10"))

    # --- پرداخت (v0.3.4) ---
    # کف/سقف مبلغ شارژ آنلاین کیف پول (تومان) — درگاه فعلی آزمایشی است و
    # درگاه واقعی بعداً در services/payment.py رجیستری می‌شود
    MIN_CHARGE: int = int(os.environ.get("PARASTOO_MIN_CHARGE", "10000"))
    MAX_CHARGE: int = int(os.environ.get("PARASTOO_MAX_CHARGE", "10000000"))

    # --- DNS اختصاصی (v0.3.9): نیم‌سرور PowerDNS کنار هسته ---
    # API پاوردی‌ان‌اس فقط روی شبکهٔ داخلی داکر در دسترس است؛ کلید را اگر env خالی
    # باشد، از فایل مشترک (volume dns_meta که کانتینر dns تولید می‌کند) می‌خوانیم
    DNS_API_URL: str = os.environ.get("PARASTOO_DNS_API_URL", "http://dns:8081/api/v1").rstrip("/")
    DNS_API_KEY: str = os.environ.get("PARASTOO_DNS_API_KEY", "").strip()
    DNS_API_KEY_FILE: str = os.environ.get("PARASTOO_DNS_KEY_FILE", "/opt/dns-meta/api-key")
    # خالی = ns1/ns2 روی دامنهٔ پایهٔ فعال (مثل ns1.zeolitecenter.ir)
    DNS_NAMESERVERS: str = os.environ.get("PARASTOO_DNS_NS", "").strip()
    DNS_DEFAULT_TTL: int = int(os.environ.get("PARASTOO_DNS_TTL", "3600"))

    # پلن‌های منابع: (محدودیت CPU هسته, RAM مگابایت, قیمت ساعتی)
    TIERS: dict = {
        "nano":   {"label": "نانو",   "cpus": 0.25, "mem": 256,  "price": 800},
        "small":  {"label": "کوچک",  "cpus": 0.5,  "mem": 512,  "price": 1500},
        "medium": {"label": "متوسط", "cpus": 1.0,  "mem": 1024, "price": 3000},
    }

    # معماری سرویس‌محور (v0.4.0): سقف سرویس به‌ازای هر پروژه
    MAX_SERVICES: int = int(os.environ.get("PARASTOO_MAX_SERVICES", "8"))

    # زیردامنه‌های رزروشده که نباید به کاربر داده شود
    RESERVED_SLUGS = {
        "www", "panel", "git", "apps", "api", "admin", "app",
        "mail", "smtp", "ns1", "ns2", "cdn", "static", "parastoo",
    }


settings = Settings()
