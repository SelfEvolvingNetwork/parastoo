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
from pathlib import Path

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


def render_dynamic(panel_domain: str, git_domain: str, tls_enabled: bool) -> str:
    eps = "      - web\n      - websecure" if tls_enabled else "      - web"
    tls_block = "      tls:\n        certResolver: le\n" if tls_enabled else ""
    return (
        "http:\n"
        "  routers:\n"
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
        + tls_block +
        "  services:\n"
        "    panel-svc:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        "          - url: \"http://core:8000\"\n"
        "    gitea-svc:\n"
        "      loadBalancer:\n"
        "        servers:\n"
        "          - url: \"http://gitea:3000\"\n"
    )


# ---------- بررسی DNS ----------

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


# ---------- اعمال تغییر دامنه ----------

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
        static_path = cfg_dir / "traefik.yml"
        old_static = static_path.read_text() if static_path.exists() else ""
        new_static = render_static(tls_enabled, acme_email)
        static_path.write_text(new_static)
        (dyn_dir / "routers.yml").write_text(
            render_dynamic(panel_d, git_d, tls_enabled))
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
