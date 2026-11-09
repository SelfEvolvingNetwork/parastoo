"""عملیات ترافیک برای ویزارد دامنه — بازنویسی پیکربندی، بررسی DNS، اعمال تغییر.

ترافیک دو نوع پیکربندی دارد:
- استاتیک (config/traefik.yml): ورودی‌ها، ریدایرکت HTTP→HTTPS و رزولور ACME.
  فقط هنگام شروع ترافیک خوانده می‌شود → پس از تغییر، کانتینر ترافیک ری‌استارت می‌شود.
- داینامیک (config/dynamic/routers.yml): روتینگ پنل و گیت روی دامنه فعال.
  ترافیک خودش watch می‌کند → بدون ری‌استارت اعمال می‌شود.

روترهای لیبلیِ اولیه (دامنه nip.io هنگام نصب) باقی می‌مانند و روتینگ داینامیک
دامنه جدید را اضافه می‌کند؛ پس هیچ لحظه‌ای پنل از دسترس خارج نمی‌شود.

الگوی ساب‌دامنهٔ بی‌نهایت (v0.3.33): به‌جای «یک گواهی برای هر سایت» (که هر نصب
یک سفارش ACME تازه می‌گشود و میدان با سقف نرخ ۴۲۹ لت‌انکریپت بسته می‌شد)،
یک گواهی وایلدکارد `*.apps.<دامنه>` با چالش DNS-01 روی نیم‌سرور خودمان
(پاوردی‌ان‌اس؛ ارائه‌دهندهٔ pdns lego) یک‌بار صادر می‌شود و بی‌نهایت ساب‌دامنهٔ
سایت را می‌پوشاند — ساخت سایت صدومین هم دیگر هیچ سفارش ACME‌ای نمی‌خواهد.
رزولور وایلدکارد جدا (wild) است و همیشه لت‌انکریپت: زروسsl شناسهٔ .ir را
رد می‌کند (v0.3.31) و چالش HTTP-01 اصلاً wildcard نمی‌پذیرد. پنل/گیت/ریشه/www
روی رزولور le (HTTP-01 با CA انتخابی مالک) می‌مانند — خرابی DNS-01 هرگز
زیرساخت را نمی‌بندد.
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
__ACME_CASERVER__
__ACME_EAB__
      httpChallenge:
        entryPoint: web
  wild:
    # v0.3.33 — وایلدکارد سایت‌ها: چالش DNS-01 روی PDNS خودمان، همیشه لت‌انکریپت
    acme:
      email: "__ACME_EMAIL__"
      storage: /letsencrypt/acme.json
      dnsChallenge:
        provider: pdns
__ACME_RESOLVERS__
"""

_REDIRECT_BLOCK = """\
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https\
"""


# CAهای ACME پشتیبانی‌شده — فقط همان‌هایی که install.sh هم می‌شناسد (v0.3.30)
_ACME_CA_DIRS = {
    "zerossl": "https://acme.zerossl.com/v2/DV90",
}


def pdns_resolver() -> str:
    """«IP:53» کانتینر dns برای کشف زون lego (v0.3.35) — یا رشتهٔ خالی.

    درس میدانی: lego پیش از هر چیز زون را با کوئری SOA از رزولور سیستم پیدا
    می‌کند؛ از ایران همان کوئری به رزولورهای عمومی گاهی SERVFAIL می‌دهد و
    سفارش وایلدکارد می‌میرد — در حالی که زون روی PDNS خودمان است و خودمان
    همیشه‌جواب‌دهیم. خطا نیست: بدون داکر/کانتینر فقط خالی برمی‌گردد (رفتار
    پیش‌فرض lego) و چرخهٔ بعدی همگام‌ساز دوباره می‌سنجد.
    """
    try:
        from . import dockerops
        t = dockerops.client().containers.get(settings.DNS_CONTAINER)
        nets = (t.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        for net in nets.values():
            ip = net.get("IPAddress") or ""
            if ip:
                return f"{ip}:53"
    except Exception:
        pass
    return ""


def render_static(tls_enabled: bool, acme_email: str) -> str:
    """قالب استاتیک ترافیک — v0.3.33: علاوه بر ریدایرکت/ایمیل، انتخاب CA و EAB
    را هم از env هسته (همان‌ها که install.sh در .env نوشته) داخل بلوک le رندر
    می‌کند. درس میدانی: پیش از این، هر اعمال دامنه در ویزارد استاتیک را از نو
    می‌نوشت و EAB زروسsl را بی‌صدا می‌بُرد — حساب لِهِ ACME بعدی به ثبت
    ناکام می‌خورد و هیچ چیز علت را نمی‌گفت. بلوک wild هیچ‌وقت caServer/EAB
    نمی‌گیرد: وایلدکارد همیشه لت‌انکریپت است (زروسsl شناسهٔ .ir را رد می‌کند)."""
    out = _STATIC_TEMPLATE.replace(
        "__WEB_REDIRECT__", _REDIRECT_BLOCK if tls_enabled else "")
    out = out.replace("__ACME_EMAIL__", acme_email or "admin@example.com")
    ca = (getattr(settings, "ACME_CA", "") or "").strip().lower()
    kid = (getattr(settings, "ACME_EAB_KID", "") or "").strip()
    hmac_s = (getattr(settings, "ACME_EAB_HMAC", "") or "").strip()
    ca_block = ""
    eab_block = ""
    if ca in _ACME_CA_DIRS and kid and hmac_s:
        ca_block = f'      caServer: "{_ACME_CA_DIRS[ca]}"'
        eab_block = (f'      eab:\n        kid: "{kid}"\n'
                     f'        hmacEncoded: "{hmac_s}"')
    r = pdns_resolver()
    resolvers_block = f'        resolvers:\n          - "{r}"\n' if r else ""
    return (out.replace("__ACME_CASERVER__", ca_block)
               .replace("__ACME_EAB__", eab_block)
               .replace("__ACME_RESOLVERS__", resolvers_block))


def render_dynamic(panel_domain: str, git_domain: str, tls_enabled: bool,
                   base_domain: str = "", wild_mode: bool = False) -> str:
    """رندر روترهای زیرساخت — wild_mode (v0.3.34): پنل/گیت/ریشه/www هم به
    وایلدکارد DNS-01 می‌روند. سه گواهی برای کل استک: `*.<دامنه>` + جفت
    `<دامنه>+www.<دامنه>` (v0.3.36) + `*.apps.<دامنه>`؛ هیچ هاستی سفارش
    اختصاصی از le ندارد.

    v0.3.36 — درس میدانی نصب ششم: apex سفارشِ مجموعهٔ دقیقِ `{دامنه}` را
    می‌داد و سقف نرخ لت‌انکریپت «۵ گواهی به‌ازای هر مجموعهٔ دقیق شناسه در
    ۱۶۸س» با سوختن همان مجموعه در نصب‌های ۳–۶، ریشه را تا یک هفته روی
    گواهی پیش‌فرض self-signed نگه می‌داشت (کاربر واقعی خطای SSL می‌بیند).
    مجموعهٔ `{<دامنه>, www.<دامنه>}` مجموعهٔ متفاوتی است — سهمیهٔ تازهٔ
    خودش را دارد و هرگز با نصب‌های قبلی تصادم نمی‌کند. وایلدکارد را به‌عنوان
    SAN نچسباندیم چون `{*.<دامنه>, <دامنه>}` هر دو شناسه‌اش روی یک FQDN
    چالش (`_acme-challenge.<دامنه>`) می‌نویسند و PDNS با «Duplicate record
    in RRset» رد می‌کند — میدان همان استقرار. www بودن در zone خودمان،
    چالش‌اش FQDN جدا دارد. تمدید هر گواهی جدا و یک‌بار در ۶۰روز.
    اولویت ۱۰۰ روترهای فایل لازم است چون روترهای لیبلیِ compose (certresolver=le)
    با همان قاعدهٔ Host می‌مانند (مسیر nip.io/بوت اول) — اگر فایل نبرد، SNI به
    روتر لیبلی می‌رسد و گواهی وایلدکارد هرگز سرو نمی‌شود. در حالت le
    (nip.io/دامنهٔ بیرونی) خروجی دقیقاً رفتار قدیم است.
    """
    eps = "      - web\n      - websecure" if tls_enabled else "      - web"
    base = (base_domain or "").strip().lower().rstrip(".")
    if tls_enabled and wild_mode and base:
        infra_tls = ("      priority: 100\n"
                     "      tls:\n"
                     "        certResolver: wild\n"
                     f"        domains:\n"
                     f"          - main: \"*.{base}\"\n")
    else:
        infra_tls = "      tls:\n        certResolver: le\n" if tls_enabled else ""
    routers = (
        "    panel-dyn:\n"
        f"      rule: \"Host(`{panel_domain}`)\"\n"
        "      entryPoints:\n"
        f"{eps}\n"
        "      service: panel-svc\n"
        + infra_tls +
        "    git-dyn:\n"
        f"      rule: \"Host(`{git_domain}`)\"\n"
        "      entryPoints:\n"
        f"{eps}\n"
        "      service: gitea-svc\n"
        + infra_tls
    )
    # v0.3.17 — ریشه/www روتر گرفتند: sync_base_zone رکورد A ریشه/www را نگه
    # می‌دارد و راهنمای DNS «وب‌سایت ریشه» را قول می‌دهد، ولی روتری برای آن‌ها
    # نبود → https://دامنهٔ ریشه گواهی پیش‌فرض ترافیک می‌گرفت. دو روتر جدا =
    # دو گواهی مستقل؛ خرابی www صدور گواهی ریشه را نمی‌بندد.
    middlewares = ""
    if base and panel_domain == f"panel.{base}":
        esc = base.replace(".", r"\.")
        target = (f"https://{panel_domain}" if tls_enabled
                  else f"http://{panel_domain}")
        # وایلدکارد فقط یک برچسب را می‌پوشاند — ریشهٔ خود دامنه apex را
        # می‌خواهد. v0.3.36: apex سفارش مستقل با SAN www می‌گیرد نه SAN
        # وایلدکارد — چون {*.z, z} هر دو شناسه روی یک FQDN چالش
        # (_acme-challenge.z) می‌نویسند و PDNS با «Duplicate record» رد
        # می‌کند (میدان همان استقرار)؛ جفتِ {z, www.z} دو FQDN چالش متفاوت
        # دارد و مجموعهٔ دقیق متفاوتش سهمیهٔ نرخ تازه می‌گیرد (سفارش apex
        # تنها 429 می‌خورد — سوختِ نصب‌های ۳–۶).
        root_tls = infra_tls
        if tls_enabled and wild_mode and base:
            root_tls = ("      priority: 100\n"
                        "      tls:\n"
                        "        certResolver: wild\n"
                        "        domains:\n"
                        f"          - main: \"{base}\"\n"
                        f"            sans:\n"
                        f"              - \"www.{base}\"\n")
        routers += (
            "    base-redir:\n"
            f"      rule: \"Host(`{base}`)\"\n"
            "      entryPoints:\n"
            f"{eps}\n"
            "      middlewares:\n"
            "      - base-redir-mw\n"
            "      service: noop@internal\n"
            + root_tls +
            "    www-redir:\n"
            f"      rule: \"Host(`www.{base}`)\"\n"
            "      entryPoints:\n"
            f"{eps}\n"
            "      middlewares:\n"
            "      - base-redir-mw\n"
            "      service: noop@internal\n"
            + infra_tls
        )
        middlewares = (
            "  middlewares:\n"
            "    base-redir-mw:\n"
            "      redirectRegex:\n"
            f"        regex: '^https?://(www\\.)?{esc}/(.*)'\n"
            f"        replacement: '{target}/$2'\n"
            "        permanent: false\n"
        )
    # v0.3.33 — روترِ اعلام وایلدکارد سایت‌ها: تنها وظیفه‌اش رساندن
    # `*.apps.<دامنه>` به رزولور wild است تا گواهی یک‌بار صادر/تمدید شود — حتی
    # وقتی هنوز هیچ سایتی ساخته نشده. priority: 1 حیاتی است: اولویت پیش‌فرض
    # ترافیک «طول قاعده» است و HostRegexp بلندتر از Host دقیق سایت‌ها
    # درمی‌آمد → دام‌الگو ترافیک همهٔ سایت‌ها را به panel-svc می‌ربود؛ با ۱
    # همهٔ روترهای دقیق برنده‌اند. فقط در حالت وایلدکارد — در حالت le
    # (nip.io) سفارش DNS-01 بی‌سرانجام می‌ماند و فقط آلودگی لاگ است.
    if base and tls_enabled and wild_mode:
        routers += (
            "    apps-wild:\n"
            f"      rule: \"HostRegexp(`{{any:.+}}.apps.{base}`)\"\n"
            "      priority: 1\n"
            "      entryPoints:\n"
            "        - websecure\n"
            "      service: panel-svc\n"
            "      tls:\n"
            "        certResolver: wild\n"
            "        domains:\n"
            f"          - main: \"*.apps.{base}\"\n"
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
    v0.3.28 — پایان lame delegation نصب تازه: اگر زون روی PDNS ما نباشد،
    خودمان آن را با kind=base provision می‌کنیم. چرا بدون گارد خارجی؟
    (۱) همهٔ مسیرهای فراخوانی این تابع نیت صریحِ ادمین‌اند (اعمال دامنه در
    ویزارد/ورکر انتظارِ همان دامنه)؛ (۲) زونی که روی PDNS ما ساخته می‌شود
    بدون دیالگیشن دنیا کاملاً بی‌اثر است — هیچ رزولوری از ما نمی‌پرسد، پس
    تهدید take-over ندارد؛ (۳) گارد خارجی («NS به ما اشاره می‌کند؟») در
    میدان ایران بی‌اعتماد است: رزولور عمومی برای زون مرده SERVFAIL خالی
    می‌دهد و سرورهای TLD/root از UDP تایم‌اوت می‌شوند (میدان 2026-11-06:
    a.nic.ir از سرور تایم‌اوت؛ 8.8.8.8 rcode=2 خالی) — دقیقاً در همان
    سناریویی که ترمیم لازم است گارد شکست می‌خورد و خودش بن‌بست می‌ساخت.
    اگر NS جای دیگری است، زونِ ساخته‌شده بی‌اثر می‌ماند و فعال‌سازی TLS هم
    به گارد check_dns (رکوردها به این سرور) باقی است — بیمهٔ دو لایه.
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
            # زون غایب → ساخت (بیمهٔ lame delegation؛ بی‌ضرر اگر NS جایی دیگر باشد)
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
            render_dynamic(panel_d, git_d, tls_enabled, base_domain=base_domain,
                           wild_mode=domains.wildcard_mode(db)))
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

    # v0.5.14 — نشانگر هویت دامنه همیشه هم‌گام: با هر اعمالِ دامنهٔ واقعی،
    # /etc/parastoo-domain هم نو می‌شود تا نصب دوبارهٔ پس از «حذف کامل»
    # (که .env را هم می‌برد) همان دامنهٔ فعال را بازسازی کند؛ nip.io موقتی
    # است و هرگز ثبت نمی‌شود. بهترین‌تلاشی: /etc فقط-خواندنی نباید مسیر را ببندد.
    try:
        if not base_domain.endswith(".nip.io"):
            Path("/etc/parastoo-domain").write_text(base_domain + "\n", encoding="utf-8")
    except Exception:
        pass

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
    # ۳.۷) قالب‌های سفارشی گیتا با URL پنل تازه (v0.5.8) — پس از set_many
    # تا domains دامنهٔ تازه را بخواند؛ ری‌استارتِ لازم داخل همان تابع است
    try:
        from .gitea import ensure_custom_templates_and_restart
        if ensure_custom_templates_and_restart():
            result["gitea_rebranded"] = True
    except Exception as e:
        result["errors"].append(f"همگام‌سازی قالب گیتا: {e}")

    # ۴) سایت‌های موجود: زیردامنهٔ جدید + استقرار مجدد پروژهٔ هر سایت
    # (سرویس‌محور v0.4.0: روتر ترافیک روی کانتینرهای پروژه است؛ با دامنهٔ
    # تازه باید همان پروژه‌ها دوباره مستقر شوند — یک‌بار برای هر پروژه)
    if Site is not None and dockerops.available():
        handled: set[int] = set()
        for site in db.query(Site).all():
            new_sub = f"{site.slug}.{apps_d}"
            if site.subdomain == new_sub:
                continue
            site.subdomain = new_sub
            if site.project_id in handled:
                continue
            handled.add(site.project_id)
            try:
                project = site.project
                dockerops.deploy_project(project, project.services, project.sites)
                for svc in project.services:
                    if svc.image:
                        svc.status = "running"
                result["sites_updated"] += 1
            except Exception as e:
                result["errors"].append(f"بازاستقرار پروژه {site.project_id}: {e}")
        db.commit()
    return result


# ---------- همگام‌سازی پیکربندی ترافیک با حقیقت (v0.3.33) ----------

CFG_RESTART_GAP = 300   # حداقل فاصلهٔ ری‌استارت ترافیک برخاسته از همگام‌سازی
_last_cfg_restart = 0.0


def ensure_traefik_config(db) -> dict:
    """پیکربندی ترافیک روی دیسک باید همیشه از قالبِ همین نسخهٔ کد + حقیقت دامنه
    ساخته شده باشد. تا v0.3.33 فقط نصب‌کننده و اعمال دامنهٔ ویزارد این فایل‌ها را
    می‌نوشتند — یعنی هر تغییر قالب در کد، تا نصب مجدد هرگز روی سرور زنده نمی‌نشست
    (استقرار همین v0.3.33 گره را نشان داد: رزولور wild تازه در فایل‌های رندرشدهٔ
    قدیمی وجود نداشت و auto-update هم فقط compose را up می‌کند). هر چرخهٔ heal
    رندر می‌کند و فقط وقتی خروجی با دیسک فرق دارد می‌نویسد؛ تغییر استاتیک =
    ری‌استارت ترافیک با گارد فاصله (که چرخهٔ ACME را هم از نو می‌اندازد)؛
    داینامیک را فایل‌پروایدر خودکار می‌گیرد. بدون استثنا — best-effort مثل
    بقیهٔ فازهای heal؛ در محیط بدون داکر فقط نوشتن انجام می‌شود.
    منبع مقادیر: دیتابیس (پس از ویزارد) وگرنه env نصب (ACME_EMAIL جدیداً به
    هسته می‌رسد) — دقیقاً همان ورودی‌هایی که install.sh برای رندر اول داشت.
    """
    global _last_cfg_restart
    out: dict = {"static": False, "dynamic": False, "restarted": False}
    try:
        from . import domains
        b = (domains.base(db) or "").strip().lower().rstrip(".")
        tls_on = bool(domains.tls(db))
        email = (domains.acme_email(db)
                 or (getattr(settings, "ACME_EMAIL", "") or "").strip()
                 or "admin@example.com")
        cfg_dir = Path(settings.CONFIG_DIR)
        dyn_dir = cfg_dir / "dynamic"
        dyn_dir.mkdir(parents=True, exist_ok=True)

        static_path = cfg_dir / "traefik.yml"
        new_static = render_static(tls_on, email)
        old_static = static_path.read_text(encoding="utf-8") if static_path.exists() else ""
        if old_static != new_static:
            static_path.write_text(new_static, encoding="utf-8")
            out["static"] = True

        dyn_path = dyn_dir / "routers.yml"
        new_dyn = render_dynamic(domains.panel(db), domains.git(db),
                                 tls_on, base_domain=b,
                                 wild_mode=domains.wildcard_mode(db))
        old_dyn = dyn_path.read_text(encoding="utf-8") if dyn_path.exists() else ""
        if old_dyn != new_dyn:
            dyn_path.write_text(new_dyn, encoding="utf-8")
            out["dynamic"] = True

        if out["static"]:
            import time as _time
            try:
                if _time.time() - _last_cfg_restart >= CFG_RESTART_GAP:
                    from . import dockerops
                    dockerops.client().containers.get(
                        settings.TRAEFIK_CONTAINER).restart(timeout=10)
                    _last_cfg_restart = _time.time()
                    out["restarted"] = True
            except Exception as e:
                out["restart_error"] = str(e)
    except Exception as e:
        out["error"] = str(e)
    return out
