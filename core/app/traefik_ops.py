"""عملیات ترافیک برای ویزارد دامنه — بازنویسی پیکربندی، بررسی DNS، اعمال تغییر.

ترافیک دو نوع پیکربندی دارد:
- استاتیک (config/traefik.yml): ورودی‌ها، ریدایرکت HTTP→HTTPS و رزولور ACME.
  فقط هنگام شروع ترافیک خوانده می‌شود → پس از تغییر، کانتینر ترافیک ری‌استارت می‌شود.
- داینامیک (config/dynamic/routers.yml): روتینگ پنل و گیت روی دامنه فعال.
  ترافیک خودش watch می‌کند → بدون ری‌استارت اعمال می‌شود.

روترهای لیبلیِ اولیه (دامنه nip.io هنگام نصب) باقی می‌مانند و روتینگ داینامیک
دامنه جدید را اضافه می‌کند؛ پس هیچ لحظه‌ای پنل از دسترس خارج نمی‌شود.
"""
import secrets
import socket
import ssl
from pathlib import Path

import httpx

from .config import settings

try:
    from .models import Site
except ImportError:  # برای تست‌های مستقل
    Site = None

_STATIC_TEMPLATE = """\
# پیکربندی استاتیک ترافیک — ساخته/به‌روزرسانی‌شده توسط پرستو
entryPoints:
  web:
    address: ":80"
__WEB_REDIRECT__
  websecure:
    address: ":443"

providers:
  docker:
    exposedByDefault: false
  file:
    directory: /etc/traefik/dynamic
    watch: true

api:
  dashboard: false

log:
  level: INFO

accessLog: {}

certificatesResolvers:
  le:
    acme:
      email: "__ACME_EMAIL__"
      storage: /letsencrypt/acme.json
      httpChallenge:
        entryPoint: web
"""

_REDIRECT_BLOCK = """\
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https\
"""


def render_static(tls_enabled: bool, acme_email: str) -> str:
    out = _STATIC_TEMPLATE.replace(
        "__WEB_REDIRECT__", _REDIRECT_BLOCK if tls_enabled else "")
    return out.replace("__ACME_EMAIL__", acme_email or "admin@example.com")


def render_dynamic(panel_domain: str, git_domain: str, tls_enabled: bool,
                   base_domain: str = "") -> str:
    eps = "      - web\n      - websecure" if tls_enabled else "      - web"
    tls_block = "      tls:\n        certResolver: le\n" if tls_enabled else ""
    routers = (
        "    panel-dyn:\n"
        f"      rule: \"Host(`{panel_domain}`)\"\n"
        "      entryPoints:\n"
        f"{eps}\n"
        "      service: panel-svc\n"
        + tls_block +
        "    git-dyn:\n"
        f"      rule: \"Host(`{git_domain}`)\"\n"
        "      entryPoints:\n"
        f"{eps}\n"
        "      service: gitea-svc\n"
        + tls_block
    )
    # v0.3.17 — ریشه/www روتر گرفتند: sync_base_zone رکورد A ریشه/www را نگه
    # می‌دارد و راهنمای DNS «وب‌سایت ریشه» را قول می‌دهد، ولی روتری برای آن‌ها
    # نبود → https://دامنهٔ ریشه گواهی پیش‌فرض ترافیک می‌گرفت. دو روتر جدا =
    # دو گواهی مستقل؛ خرابی www صدور گواهی ریشه را نمی‌بندد.
    base = (base_domain or "").strip().lower().rstrip(".")
    middlewares = ""
    if base and panel_domain == f"panel.{base}":
        esc = base.replace(".", r"\.")
        target = (f"https://{panel_domain}" if tls_enabled
                  else f"http://{panel_domain}")
        routers += (
            "    base-redir:\n"
            f"      rule: \"Host(`{base}`)\"\n"
            "      entryPoints:\n"
            f"{eps}\n"
            "      middlewares:\n"
            "      - base-redir-mw\n"
            "      service: noop@internal\n"
            + tls_block +
            "    www-redir:\n"
            f"      rule: \"Host(`www.{base}`)\"\n"
            "      entryPoints:\n"
            f"{eps}\n"
            "      middlewares:\n"
            "      - base-redir-mw\n"
            "      service: noop@internal\n"
            + tls_block
        )
        middlewares = (
            "  middlewares:\n"
            "    base-redir-mw:\n"
            "      redirectRegex:\n"
            f"        regex: '^https?://(www\\.)?{esc}/(.*)'\n"
            f"        replacement: '{target}/$2'\n"
            "        permanent: false\n"
        )
    return (
        "http:\n"
        "  routers:\n"
        + routers +
        "  services:\n"
        "    panel-svc:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        "          - url: \"http://core:8000\"\n"
        "    gitea-svc:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        "          - url: \"http://gitea:3000\"\n"
        + middlewares
    )


# ---------- پروب HTTPS (v0.3.15) ----------

# برای تست‌ها: transport اجباری — در تولید همیشه None
_PROBE_TRANSPORT: httpx.BaseTransport | None = None


def probe_https(host: str) -> str:
    """پروب سرتاسری HTTPS پنل روی دامنهٔ داده‌شده — خروجی همیشه یکی از سه رشته:
    ok     = دست‌دادن TLS سالم بود و پاسخ زیر ۵۰۰ آمد (گواهی صادر و معتبر است)
    pending= DNS/شبکه/گواهی هنوز آماده نیست (اتصال برقرار نشد)
    error  = وصل شد ولی پاسخ ۵xx (سرویس پشت گواهی مشکل دارد)
    بدون استثنا — این پروب فقط «وضعیت» می‌گوید، هرگز مسیر فعال‌سازی را نمی‌بندد.
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return "pending"
    url = f"https://{h}/healthz"
    try:
        with httpx.Client(timeout=8.0, verify=True,
                          transport=_PROBE_TRANSPORT) as cli:
            r = cli.get(url)
        return "ok" if r.status_code < 500 else "error"
    except Exception:
        return "pending"


# ---------- وضعیت گواهی سایت‌ها (v0.3.23) ----------

def site_cert_ok(subdomain: str) -> bool | None:
    """آیا ترافیک برای این هاست گواهی معتبر و منطبق دارد؟

    اتصال مستقیم به کانتینر ترافیک روی شبکهٔ داکر (بدون DNS عمومی و بدون
    وابستگی به hairpin) با SNI همان زیردامنه و اعتبارسنجی کامل زنجیره:
    True  = دست‌دادن TLS سالم بود — گواهی صادر شده و برای همین هاست است
    False = خطای تأیید گواهی — هنوز صادر نشده یا ناهم‌خوان است
    None  = قابل بررسی نبود (ترافیک/شبکه در دسترس نیست) — تصمیم نگیر
    بدون استثنا — فراخواننده فقط وضعیت می‌خواهد، نه ردیابی خطا.
    """
    h = (subdomain or "").strip().lower().rstrip(".")
    if not h:
        return None
    try:
        from . import dockerops  # واردسازی دیرهنگام — دورِ تست بدون داکر
        c = dockerops.client()
        t = c.containers.get(settings.TRAEFIK_CONTAINER)
        nets = (t.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        ip = ""
        for net in nets.values():
            ip = net.get("IPAddress") or ""
            if ip:
                break
        if not ip:
            return None
    except Exception:
        return None
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((ip, 443), timeout=6.0) as raw:
            with ctx.wrap_socket(raw, server_hostname=h):
                return True
    except ssl.SSLCertVerificationError:
        return False  # گواهی پیش‌فرض/ناهم‌خوان — یعنی هنوز صادر نشده
    except Exception:
        return None


# ---------- بررسی DNS ----------

def resolve_a(host: str) -> str | None:
    """A رکورد عمومی یک هاست — برای گارد خودترمیم گواهی (v0.3.23)."""
    return _resolve_a(host)


def _resolve_a(host: str) -> str | None:
    try:
        infos = socket.getaddrinfo(host, 80, family=socket.AF_INET)
        return infos[0][4][0] if infos else None
    except Exception:
        return None


def check_dns(base_domain: str, server_ip: str) -> list[dict]:
    """وضعیت سه رکورد لازم (پنل، گیت، وایلدکارد سایت‌ها) نسبت به IP سرور."""
    base_domain = base_domain.strip().lower()
    probe = f"probe-{secrets.token_hex(3)}.apps.{base_domain}"
    out = []
    for label, host in (
        ("پنل", f"panel.{base_domain}"),
        ("گیت داخلی", f"git.{base_domain}"),
        ("وایلدکارد سایت‌ها", probe),
    ):
        resolved = _resolve_a(host)
        out.append({
            "label": label,
            "host": host,
            "expected": server_ip,
            "resolved": resolved,
            "ok": bool(server_ip and resolved and resolved == server_ip),
        })
    return out


# ---------- خودترمیمی زون پایه (v0.3.15) ----------

def sync_base_zone(db, base_domain: str) -> str:
    """همگام‌سازی A رکوردهای زون پایه روی نیم‌سرور خودمان با IP سرور.

    حلقهٔ کامل دامنه: زون (v0.3.9 provision) → فعال‌سازی (ویزارد/ورکر v0.3.14).
    تا v0.3.15 این دو سرِ کوه بودند: اگر IP سرور عوض می‌شد یا رکوردهای زون
    از فعال‌سازی جا می‌ماندند، check_dns برای همیشه شکست می‌خورد بی‌آنکه کسی
    بفهمد چرا. اکنون هر apply_domain اگر زون پایه روی PDNS خودمان باشد،
    ریشه/www/panel/git/apps/*.apps را با IP زنده بازنویسی می‌کند.
    v0.3.28 — پایان lame delegation نصب تازه: اگر زون روی PDNS ما نباشد ولی
    NS دامنه به همین سرور اشاره کند، دنیای بیرون ما را نگهبان DNS می‌داند و
    زونِ غایب یعنی REFUSED برای کل دامنه (پنل/گیت/سایت‌ها از دسترس می‌افتند؛
    میدان 2026-11-06: پس از نصب از صفر، وایلدکارد resolve نمی‌شد و ویزارد
    بن‌بست می‌شد). در این حالت خودمان زون را با kind=base provision می‌کنیم.
    اگر NS جای دیگری است، زون واقعاً جای دیگری میزبانی می‌شود → بی‌صدا رد.
    این گام بیمه است، نه پیش‌نیاز — هرگز استثنا نمی‌دهد و مسیر را نمی‌بندد.
    """
    from . import domains
    from .services import dnsops

    z = (base_domain or "").strip().lower().rstrip(".")
    if not z:
        return "no_domain"
    try:
        if not dnsops.available():
            return "dns_unavailable"
        try:
            dnsops.get_zone(z)
        except dnsops.DnsError as e:
            if e.status != 404:
                return f"zone_check_failed:{e.status}"
            # زون غایب — آیا دنیا ما را نگهبان این دامنه می‌داند؟
            if not _delegation_points_here(z, db):
                return "no_zone"
            try:
                dnsops.provision_zone(z, db=db, kind="base")
            except dnsops.DnsError as pe:
                if pe.status == 409:  # بین چک و ساخت، جای دیگری ساخت — بی‌خیال
                    pass
                else:
                    return f"zone_provision_failed:{pe.status}"
            except dnsops.DnsUnavailable:
                return "dns_unavailable"
        except dnsops.DnsUnavailable:
            return "dns_unavailable"
        ip = domains.server_ip(db)
        if not ip:
            return "no_ip"
        n = 0
        for name in ("", "www", "panel", "git", "apps", "*.apps"):
            try:
                dnsops.upsert_record(z, name, "A", ip)
                n += 1
            except Exception:
                pass  # یک رکورد ناموفق نباید بقیه یا مسیر فعال‌سازی را ببندد
        return f"updated:{n}/6"
    except Exception as e:
        return f"error:{type(e).__name__}"


def _delegation_points_here(zone: str, db) -> bool:
    """آیا دنیا این سرور را نگهبان DNS این دامنه می‌داند؟ (گارد provision v0.3.28)

    امضای رسمی پرستو: زون‌های میزبانی‌شده روی این نیم‌سرور glue NS دارند —
    ns1.<zone> و ns2.<zone> به IP همین سرور اشاره می‌کنند (الگوی provision_zone).
    اگر حداقل یکی از آن‌ها با getaddrinfo (همان سازوکار check_dns) به IP سرور
    رسید، زونِ غایب روی PDNS ما یعنی lame delegation و ساختش ترمیم است؛
    در غیر این صورت زون جای دیگری میزبانی می‌شود → دست نمی‌زنیم.
    """
    import socket

    from . import domains

    try:
        ip = domains.server_ip(db)
        if not ip:
            return False
        hits = 0
        for ns in (f"ns1.{zone}", f"ns2.{zone}"):
            try:
                infos = socket.getaddrinfo(ns, 53, family=socket.AF_INET)
            except Exception:
                continue
            if any(ai[4][0] == ip for ai in infos):
                hits += 1
        return hits >= 1
    except Exception:
        return False


# ---------- اعمال تغییر دامنه ----------

def clean_dynamic_dir() -> list[str]:
    """حذف فایل‌های پنهان/موقت از پوشهٔ داینامیک ترافیک — خودترمیمی v0.3.16.

    درس میدانی: فایل‌پروایدر ترافیک همهٔ فایل‌های پوشه را می‌خواند و یک
    فایل جا مانده از نسخه‌های اولیه (.parastoo-sync.yml — نشانگر sync
    طرح قدیمی) کل بیلد کانفیگ را می‌شکند: «routers cannot be a standalone
    element» → نه روتری ساخته می‌شود (حتی با routers.yml سالم) و نه
    گواهی‌ای صادر می‌شود؛ تنها علامت، خطای ERR در لاگ ترافیک است.
    فایل‌های آشکار .yml (احتمالاً پیکربندی دستی مالک) حذف نمی‌شوند.
    """
    removed: list[str] = []
    dyn = Path(settings.CONFIG_DIR) / "dynamic"
    if not dyn.is_dir():
        return removed
    for f in sorted(dyn.iterdir()):
        if f.is_file() and (f.name.startswith(".")
                            or f.name.endswith((".tmp", ".bak", "~"))):
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                continue  # یک فایل مقاوم نباید بقیه یا فراخوان را ببندد
    return removed


def apply_domain(db, base_domain: str, acme_email: str, tls_enabled: bool) -> dict:
    """اعمال دامنه جدید: فایل‌ها + دیتابیس + ترافیک + سایت‌های موجود."""
    from . import dockerops, domains

    result = {"files": False, "static_changed": False, "traefik_restarted": False,
              "sites_updated": 0, "errors": []}
    base_domain = base_domain.strip().lower()
    panel_d = f"panel.{base_domain}"
    git_d = f"git.{base_domain}"
    apps_d = f"apps.{base_domain}"

    # ۱) نوشتن پیکربندی‌ها روی دیسک هاست (سوارشده در ترافیک)
    cfg_dir = Path(settings.CONFIG_DIR)
    dyn_dir = cfg_dir / "dynamic"
    try:
        dyn_dir.mkdir(parents=True, exist_ok=True)
        result["dynamic_cleaned"] = clean_dynamic_dir()  # v0.3.16 — پیش از نوشتن
        static_path = cfg_dir / "traefik.yml"
        old_static = static_path.read_text() if static_path.exists() else ""
        new_static = render_static(tls_enabled, acme_email)
        static_path.write_text(new_static)
        (dyn_dir / "routers.yml").write_text(
            render_dynamic(panel_d, git_d, tls_enabled, base_domain=base_domain))
        result["files"] = True
        result["static_changed"] = old_static != new_static
    except Exception as e:
        result["errors"].append(f"نوشتن پیکربندی ترافیک: {e}")
        return result

    # ۲) پیکربندی استاتیک فقط با ری‌استارت اعمال می‌شود (داینامیک خودکار است)
    if result["static_changed"]:
        try:
            c = dockerops.client()
            c.containers.get(settings.TRAEFIK_CONTAINER).restart(timeout=10)
            result["traefik_restarted"] = True
        except Exception as e:
            result["errors"].append(f"ری‌استارت ترافیک: {e}")

    # ۳) ذخیره در دیتابیس (منبع حقیقت دامنه فعال از این پس)
    domains.set_many({
        "domain_base": base_domain,
        "domain_panel": panel_d,
        "domain_git": git_d,
        "domain_apps": apps_d,
        "domain_tls": "1" if tls_enabled else "0",
        "domain_acme_email": acme_email,
    }, db)

    # ۳.۵) خودترمیمی زون پایه روی PDNS خودمان (v0.3.15) — اگر زون اینجاست،
    # رکوردها با IP زنده همگام می‌شوند تا حلقهٔ provision→activate بسته بماند
    try:
        result["zone_sync"] = sync_base_zone(db, base_domain)
    except Exception:
        result["zone_sync"] = "error"

    # ۳.۶) هم‌خوانی گیتا و .env هاست با دامنهٔ تازه (v0.3.22) — اگر گیتا با env
    # نصب اول بماند، چالش رجیستری داکر (realm) به دامنهٔ کهنه برمی‌گردد و
    # docker login هر بیلد به 404 می‌خورد؛ .env هم باید تازه شود تا یک up
    # بعدی compose همه‌چیز را به نصب اول پس نزند.
    try:
        result["host_env"] = dockerops.sync_host_env()
    except Exception as e:
        result["errors"].append(f"همگام‌سازی .env هاست: {e}")
    try:
        stale, cur = dockerops.gitea_stale()
        if stale:
            dockerops.recreate_gitea()
            result["gitea_recreated"] = True
            result["gitea_old_root_url"] = cur
    except Exception as e:
        result["errors"].append(f"بازسازی گیتا: {e}")

    # ۴) سایت‌های موجود: زیردامنه جدید + بازسازی کانتینر با لیبل‌های جدید
    if Site is not None and dockerops.available():
        for site in db.query(Site).all():
            new_sub = f"{site.slug}.{apps_d}"
            if site.subdomain == new_sub:
                continue
            site.subdomain = new_sub
            try:
                dockerops.recreate_site(site)
                result["sites_updated"] += 1
            except Exception as e:
                result["errors"].append(f"بازسازی سایت {site.slug}: {e}")
        db.commit()
    return result
