"""پیکربندی پرستو — همه مقادیر از متغیرهای محیطی خوانده می‌شوند (توسط install.sh در .env نوشته می‌شود)."""
import os


def _bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- پایه ---
    APP_NAME: str = "پرستو"
    VERSION: str = "0.2.1"
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

    # --- ویزارد نصب (v0.2) ---
    # توکن یک‌بارمصرف نصب — فقط در خروجی install.sh چاپ می‌شود و گیتِ ویزارد وب است
    SETUP_TOKEN: str = os.environ.get("PARASTOO_SETUP_TOKEN", "")
    # IP عمومی سرور (install.sh تشخیص می‌دهد) — برای بررسی رکوردهای DNS در ویزارد
    PUBLIC_IP: str = os.environ.get("PARASTOO_PUBLIC_IP", "")
    # پوشه پیکربندی ترافیک روی هاست که داخل کانتینر سوار شده — ویزارد دامنه آن را بازنویسی می‌کند
    CONFIG_DIR: str = os.environ.get("PARASTOO_CONFIG_DIR", "/app/host-config")
    TRAEFIK_CONTAINER: str = os.environ.get("PARASTOO_TRAEFIK_CONTAINER", "parastoo-traefik")

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

    # پلن‌های منابع: (محدودیت CPU هسته, RAM مگابایت, قیمت ساعتی)
    TIERS: dict = {
        "nano":   {"label": "نانو",   "cpus": 0.25, "mem": 256,  "price": 800},
        "small":  {"label": "کوچک",  "cpus": 0.5,  "mem": 512,  "price": 1500},
        "medium": {"label": "متوسط", "cpus": 1.0,  "mem": 1024, "price": 3000},
    }

    # فناوری‌های مجاز برای ساخت پروژه (لیست محدود برای مدیریت‌پذیری)
    STACKS: dict = {
        "static":  {"label": "استاتیک (HTML/CSS/JS — nginx)"},
        "node":    {"label": "Node.js 20 (باید روی پورت 3000 گوش دهد)"},
        "python":  {"label": "Python 3.12 (باید روی پورت 3000 گوش دهد)"},
        "docker":  {"label": "داکرفایل اختصاصی خودم (پورت 3000)"},
    }

    # زیردامنه‌های رزروشده که نباید به کاربر داده شود
    RESERVED_SLUGS = {
        "www", "panel", "git", "apps", "api", "admin", "app",
        "mail", "smtp", "ns1", "ns2", "cdn", "static", "parastoo",
    }


settings = Settings()
