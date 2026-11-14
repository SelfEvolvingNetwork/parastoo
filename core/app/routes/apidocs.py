"""مرجع فنی خودمعرف پنل — core/app/routes/apidocs.py (v0.5.30).

GET /api/docs (عمومی، بدون احراز) — «پاکت هم‌شکل» خانوادهٔ پرستو (قرارداد
docs/FAMILY_PACKETS.fa.md؛ توافق مالک–عامل ۲۰۲۶-۱۱-۱۴): خواهرِ همان
اندپوینت در پل access-agent (access_agent/apidocs.py) با شکل واحد.

دو اصل قرارداد: (۱) پاکت نقشه است، نه محتوا — شرح عمیق هر سطح فقط در
سند مرجع خودش زندگی می‌کند (INDEX §۳) و این‌جا فقط آدرس می‌شود؛ ضد
کهنگی. (۲) کد یک سرویس فقط وقتی خوانده می‌شود که رفتار با قرارداد
ناسازگار باشد؛ داورِ «باگ کد یا کهنگی قرارداد» اسموک‌ها (تست قراردادی)‌اند.

همه‌چیز داده است — هیچ راز، هیچ متغیر محیطی حساس. نقش هر مخزن عیناً از
جدول INDEX §۱ (مبدأ)؛ خودارجاعی: مدخل parastoo خودش URL زندهٔ همین
اندپوینت را می‌دهد.
"""
from fastapi import APIRouter
from starlette.responses import JSONResponse

from ..config import settings

router = APIRouter()

_GITHUB = "https://github.com/SelfEvolvingNetwork"

# نقش‌ها عیناً از جدول INDEX §۱ — مبدأ همان‌جاست، این‌ها رونوشت‌اند
_FAMILY_SERVICES = [
    {
        "name": "access-agent",
        "kind": "سرویس زنده (systemd، بیرون از داکر)",
        "role": ("پل ادمین مستقل — تنها درگاه فرمان عامل به سرور "
                 "(از مرگ پرستو جان می‌بارد)"),
        "repo": f"{_GITHUB}/access-agent",
        "docs": ("GET /api/docs روی خود پل (پاکت هم‌شکل؛ آدرس پل از "
                "دعوت‌نامه/بلوک تحویل پل می‌آید)"),
    },
    {
        "name": "parastoo",
        "kind": "سرویس زنده (داکر)",
        "role": ("هستهٔ پلتفرم سایت‌ساز: پنل، پل عامل پروژه، DNS/TLS، "
                 "بیلد و انتشار"),
        "repo": f"{_GITHUB}/parastoo",
        "docs": "GET /api/docs همین سرویس (پاکت هم‌شکل)",
        "docs_url": f"https://{settings.PANEL_DOMAIN}/api/docs",
    },
    {
        "name": "parastoo-workbench",
        "kind": "ابزار فقط-سرور (بدون سرویس HTTP — سند مرجع، نه اندپوینت)",
        "role": ("کارگاه/سندباکس دائمی عامل روی سرور مالک "
                 "(توسعهٔ زادهٔ سرور)"),
        "repo": f"{_GITHUB}/parastoo-workbench",
        "docs": f"{_GITHUB}/parastoo-workbench/blob/main/docs/WORKBENCH.fa.md",
    },
    {
        "name": "parastoo-mobile",
        "kind": "اپ اندروید (مصرف‌کنندهٔ API — سرویس نیست)",
        "role": "اپ اندروید مدیریت و مانیتورینگ سرور",
        "repo": f"{_GITHUB}/parastoo-mobile",
        "docs": f"{_GITHUB}/parastoo-mobile/blob/main/README.md",
    },
]

_COLD_START_STEPS = [
    ("۱) نقش خود را از ROLES.fa.md انتخاب کن (A1/A2/I1..I5 یا انسان) و "
     "فقط مسیر همان نقش را بخوان — قاعدهٔ ضد توکن‌سوزی"),
    ("۲) پاکت docs سرویسی که با آن کار می‌کنی (همین پاکت): قلمرو نقش، "
     "اندپوینت‌ها، خطاها، سقف‌ها، خطوط قرمز"),
    ("۳) برای هر سرویس/مخزن دیگر خانواده فقط فیلد docs همان سرویس را "
     "بخوان (family.services) — نه کل مخزن را"),
    ("۴) کلیت در ۶۰ ثانیه: ECOSYSTEM.fa.md · نقشهٔ کامل «موضوع → سند "
     "مرجع»: INDEX.fa.md §۳"),
]


@router.get("/api/docs")
def api_docs():
    """پاکت هم‌شکل خانواده — نقشه است، نه محتوا (شرح هر سطح: سند مرجعش)."""
    return JSONResponse({
        "ok": True,
        "service": "parastoo",
        "version": settings.VERSION,
        "language": "fa",
        "updated_for": "v0.5.30 (پاکت هم‌شکل خانواده)",

        "overview": (
            "هستهٔ پلتفرم سایت‌ساز پرستو: پنل کاربر/ادمین، بیلد کل-یا-هیچ و "
            "انتشار سایت‌ها، DNS/TLS خوداتکا (PowerDNS + traefik + ACME)، "
            "پل عامل پروژه (/api/bridge/*) و تیکتینگ عضو‌به‌عضو. کاربر "
            "نهایی در پنل پروژه می‌سازد و بدون دانستن چیزی از سرور، سایتش "
            "روی <slug>.apps.<دامنه> منتشر می‌شود."
        ),

        "roles": {
            "public": {
                "scope": ["/healthz", "/api/version", "/api/docs",
                          "/docs/agent", "/download", "/pricing"],
                "description": "سلامت، نسخه، همین پاکت و صفحهٔ راهنمای عامل کاربر — بدون احراز",
            },
            "panel_session": {
                "scope": ["/dashboard", "/projects*", "/sites*",
                          "/marketplace*", "/wallet*", "/admin*", "/oauth/*",
                          "/git/sso", "/tickets* (UI)"],
                "description": ("کاربر/ادمینِ واردشدهٔ پنل با کوکی نشست "
                                "(parastoo_session) — ثبت‌نام/ورود از /auth"),
            },
            "project_agent": {
                "scope": ["/api/bridge/*", "/api/tickets*", "/bridge/git/{repo}"],
                "description": ("عامل پروژهٔ عضو (A2) با توکن pst_ — جفت‌سازی "
                                "با دعوت از صفحهٔ پروژه؛ قرارداد کامل: "
                                "docs/AGENT_BRIDGE.fa.md"),
            },
            "ecosystem_agent": {
                "scope": ["/api/tickets*"],
                "description": ("عضو درجه‌یک «agent» با توکن pat_ (ساختهٔ "
                                "خودِ سرویس، مستقل از توکن پل و ادمین) — "
                                "کانال رسمی همکاری اعضا: تیکتینگ"),
            },
            "dev_console": {
                "scope": ["/api/dev/report", "/api/dev/logs",
                          "/api/dev/updates", "/api/dev/netcheck",
                          "/api/dev/sql", "/dev"],
                "description": ("کنسول توسعه (همیشه روشن؛ v0.5.29) — ادمینِ "
                                "پنل یا هدر X-Dev-Token؛ دورزدن ممنوع"),
            },
        },

        "endpoints": [
            {"surface": "عمومی", "auth": "ندارد",
             "paths": ["/healthz", "/api/version", "/api/docs", "/docs/agent",
                       "/download", "/download/parastoo-mobile.apk", "/pricing"],
             "reference": "—"},
            {"surface": "پل عامل پروژه", "auth": "Bearer pst_",
             "paths": ["/api/bridge/status", "/api/bridge/manifest",
                       "/api/bridge/builds", "/api/bridge/builds/{bid}",
                       "/api/bridge/build", "/api/bridge/logs",
                       "/api/bridge/exec", "/api/bridge/restart",
                       "/api/bridge/env", "/api/bridge/audit"],
             "reference": "docs/AGENT_BRIDGE.fa.md"},
            {"surface": "شیم گیت عامل پروژه", "auth": "Bearer pst_",
             "paths": ["/bridge/git/{repo_path}"],
             "reference": "docs/AGENT_BRIDGE.fa.md"},
            {"surface": "جفت‌سازی عامل پروژه", "auth": "عمومی (دعوت یک‌بارمصرف از صفحهٔ پروژه)",
             "paths": ["/api/agent/onboarding", "/api/agent/claim",
                       "/api/agent/claim/poll"],
             "reference": "docs/AGENT_BRIDGE.fa.md"},
            {"surface": "تیکتینگ عضو‌به‌عضو", "auth": "Bearer pst_ یا pat_ یا نشست پنل",
             "paths": ["/api/tickets", "/api/tickets/summary",
                       "/api/tickets/destinations", "/api/tickets/{tid}",
                       "/api/tickets/{tid}/comments", "/api/tickets/{tid}/status"],
             "reference": "docs/TICKETING.fa.md"},
            {"surface": "کنسول توسعه", "auth": "ادمین پنل یا X-Dev-Token",
             "paths": ["/api/dev/report", "/api/dev/logs", "/api/dev/updates",
                       "/api/dev/netcheck", "/api/dev/sql", "/dev"],
             "reference": "—"},
            {"surface": "وب‌هوک", "auth": "مسیر مخفی پروژه",
             "paths": ["/api/hooks/gitea/{project_id}"],
             "reference": "README همین مخزن"},
        ],

        "errors": {
            "model": "FastAPI: {\"detail\": \"<توضیح>\"} (خطای اعتبارسنجی 422 با فهرست فیلدها)",
            "codes": {
                "401": "unauthorized — احراز مفقود/نامعتبر (مثلاً توکن pst_/pat_/نشست)",
                "403": "forbidden — نقش کافی نیست",
                "404": "not_found — مسیر/منبع ناشناخته",
                "405": "method_not_allowed",
                "422": "validation_error — بدنه/پارامتر نامعتبر",
                "429": "rate_limited — سقف نرخ (پل/عامل اکوسیستم) پر شده",
            },
        },

        "limits": {
            "bridge_exec_timeout_s_max": 120,
            "bridge_exec_output_bytes_max": 262144,
            "bridge_logs_tail_lines_max": 2000,
            "bridge_logs_bytes_max": 102400,
            "git_push_bytes_max": 209715200,
            "agentpair_pending_per_project_max": 4,
            "agentpair_rate_max_per_300s": 5,
            "dev_sql_rows_max": 200,
        },

        "conventions": {
            "content_type": "application/json; charset=utf-8",
            "audit": "پل و تیکتینگ هر فراخوانی عامل را در audit خود ثبت می‌کنند — بدون مقدار توکن/iid",
            "secrets": "مقدار هیچ توکنی هرگز در پاسخ/لاگ/سند نمی‌آید — فقط پیشوند (مثل pst_…)",
        },

        "red_lines": [
            "عملیات غیرقابل‌بازگشت (حذف پروژه/سایت، factory-reset، تغییر دامنه/TLS، تغییر ادمین، درگاه پرداخت واقعی) فقط با تأیید صریح مالک.",
            "پوش بدون چک‌های سبز ممنوع — پوش یعنی انتشار (تایمر autoupdate ≤~۳ دقیقه استقرار می‌دهد).",
            ".env و config/ و backups/ و logs/ سرور از راه دور باز نمی‌شوند؛ توکن/رمز فقط در .secrets/ (chmod 600، gitignored).",
        ],

        "family": {
            "name": "پرستو (SelfEvolvingNetwork)",
            "principle": (
                "هر سرویس زندهٔ خانواده یک مرجع فنی خودمعرف دارد "
                "(GET /api/docs؛ هم‌شکل: service/version/language/overview/"
                "roles/endpoints/errors/limits/family/cold_start) و هر مخزن هم "
                "سند مرجع یکتای خود را. عامل سرد نقش خود را از ROLES انتخاب "
                "می‌کند و فقط مسیر نقش خودش را می‌خواند؛ متن بیداری یکتا: "
                "ECOSYSTEM. پاکت نقشه است، نه محتوا — شرح عمیق هر سطح فقط در "
                "سند مرجع خودش زندگی می‌کند. کد یک سرویس فقط وقتی خوانده "
                "می‌شود که رفتار با این قرارداد ناسازگار باشد؛ داورِ «باگ "
                "کد یا کهنگی قرارداد» تست‌های قراردادی (اسموک)‌اند."
            ),
            "registry": f"{_GITHUB}/parastoo/blob/main/docs/INDEX.fa.md",
            "ecosystem": f"{_GITHUB}/parastoo/blob/main/docs/ECOSYSTEM.fa.md",
            "roles": f"{_GITHUB}/parastoo/blob/main/docs/ROLES.fa.md",
            "contract": f"{_GITHUB}/parastoo/blob/main/docs/FAMILY_PACKETS.fa.md",
            "services": _FAMILY_SERVICES,
        },

        "cold_start": {"steps": list(_COLD_START_STEPS)},

        "docs": f"https://{settings.PANEL_DOMAIN}/api/docs",
    })
