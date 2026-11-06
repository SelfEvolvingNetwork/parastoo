"""DNS اختصاصی پرستو (v0.3.9) — سرویس‌کار PowerDNS Authoritative (نیم‌سرور خودمان).

چرا PowerDNS؟ سرور authoritative با REST API کامل؛ هستهٔ پرستو هیچ شلی اجرا
نمی‌کند (همان قاعدهٔ طلایی dockerops) و فقط با HTTP با آن حرف می‌زند.
سرویس dns در docker-compose کنار هسته بالا می‌آید و API آن فقط روی شبکهٔ
داخلی داکر در دسترس است (پورت ۸۰۸۱ پابلیش نمی‌شود).

مسئولیت‌ها:
- اتصال به API پاوردی‌ان‌اس (X-API-Key؛ کلید از env یا فایل مشترک volume)
- ساخت زون با پیش‌فرض‌های منطقی (NS/A/www و برای دامنهٔ اصلی: panel/git/apps)
- CRUD رکورد با اعتبارسنجی سخت‌گیرانه (سفید-فهرست انواع + قالب محتوا)
- حذف زون و حذف رکورد

نکتهٔ SOA: موقع ساخت زون، SOA را صریح بازنویسی می‌کنیم تا MNAME/RNAME
منطقی شوند (پیش‌فرض پاوردی‌ان‌اس «a.misconfigured.powerdns.soa» است).
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
from pathlib import Path

import httpx

from .. import domains
from ..config import settings
from ..validation import valid_domain

# انواع رکورد مجاز — سفید-فهرست بسته (SOA/RRSIG و... از این مسیر مدیریت نمی‌شوند)
RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA")

_MAX_CONTENT = 4096
# نام رکورد: برچسب‌های لاتین/عدد/خط‌تیره/زیرخط + پیشوند وایلدکارد + نقطهٔ پایانی اختیاری
# (زیرخط برای رکوردهای سیستم مثل _dmarc و _acme-challenge لازم است)
_NAME_RE = re.compile(
    r"^(\*\.)?(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z]{2,63}\.?$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$")

# برای تست‌ها: transport اجباری (httpx.MockTransport) — در تولید همیشه None
_FORCE_TRANSPORT: httpx.BaseTransport | None = None


class DnsError(Exception):
    """خطای معنایی DNS (پاسخ 4xx/5xx پاوردی‌ان‌اس یا دادهٔ نامعتبر)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class DnsUnavailable(Exception):
    """سرویس DNS در دسترس نیست (کانتینر dns پایین است یا شبکه قطع است)."""


# ---------- اتصال ----------

def _key() -> str:
    """کلید API: اول env (PARASTOO_DNS_API_KEY)، بعد فایل مشترک volume."""
    if settings.DNS_API_KEY:
        return settings.DNS_API_KEY
    try:
        return Path(settings.DNS_API_KEY_FILE).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _client(timeout: float = 6.0) -> httpx.Client:
    key = _key()
    headers = {"X-API-Key": key} if key else {}
    return httpx.Client(
        base_url=settings.DNS_API_URL, headers=headers,
        timeout=timeout, transport=_FORCE_TRANSPORT)


def _req(method: str, path: str, json_body=None):
    try:
        with _client() as cli:
            r = cli.request(method, path, json=json_body)
    except httpx.RequestError:
        raise DnsUnavailable("سرویس DNS در دسترس نیست (کانتینر dns پایین است؟)")
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", "")
        except Exception:
            detail = (r.text or "")[:200]
        raise DnsError(detail or f"HTTP {r.status_code}", status=r.status_code)
    if r.status_code == 204 or not r.content:
        return {}
    try:
        return r.json()
    except Exception:
        return {}


# ---------- نرمال‌سازی نام‌ها ----------

def _fqdn(name: str) -> str:
    """نرمال‌سازی به FQDN: حروف کوچک، بدون فاصله، همیشه با نقطهٔ پایانی."""
    n = (name or "").strip().lower().rstrip(".")
    return n + "."


def _zone_url(zone: str) -> str:
    return "/servers/localhost/zones/" + urllib.parse.quote(_fqdn(zone), safe=".")


def _record_fqdn(zone: str, name: str) -> str:
    """نام نسبی رکورد داخل زون → FQDN. «@» یا خالی = ریشهٔ زون؛ «*.apps» نسبی است."""
    n = (name or "").strip().lower()
    z = _fqdn(zone)
    zb = z.rstrip(".")
    if n in ("", "@", "."):
        return z
    n = n.rstrip(".")
    if n == zb or n.endswith("." + zb):
        return n + "."
    if n == "*":
        return "*." + z
    if n.startswith("*."):
        tail = n[2:]
        return (f"*.{tail}.{zb}.") if tail else "*." + z
    return n + "." + zb + "."


# ---------- اعتبارسنجی ----------

def record_error(name: str, rtype: str, content: str) -> str | None:
    """خطای فارسی قالب رکورد — یا None یعنی سالم."""
    rtype = (rtype or "").upper().strip()
    if rtype not in RECORD_TYPES:
        return "نوع رکورد پشتیبانی نمی‌شود؛ انواع مجاز: " + "/".join(RECORD_TYPES)
    if not _NAME_RE.match((name or "").strip().lower()):
        return "قالب نام رکورد درست نیست (مثال: @ یا www یا *.apps یا mail.example.com)"
    c = (content or "").strip()
    if not c or len(c) > _MAX_CONTENT:
        return "محتوای رکورد خالی است یا بیش از حد بلند است"
    try:
        if rtype == "A":
            ipaddress.IPv4Address(c)
        elif rtype == "AAAA":
            ipaddress.IPv6Address(c)
        elif rtype in ("NS", "CNAME"):
            if not _HOST_RE.match(c.lower()):
                return "مقدار باید یک نام میزبان معتبر باشد (مثل example.com یا target.example.com)"
        elif rtype == "MX":
            parts = c.split()
            if (len(parts) != 2 or not parts[0].isdigit()
                    or not 0 <= int(parts[0]) <= 65535
                    or not _HOST_RE.match(parts[1].lower())):
                return "قالب MX: «اولویت میزبان» مثل 10 mail.example.com"
        elif rtype == "SRV":
            parts = c.split()
            if (len(parts) != 4 or any(not p.isdigit() for p in parts[:3])
                    or not _HOST_RE.match(parts[3].lower())):
                return "قالب SRV: «اولویت وزن پورت هدف» مثل 10 5 5060 sip.example.com"
        elif rtype == "CAA":
            parts = c.split(maxsplit=2)
            if (len(parts) != 3 or not parts[0].isdigit()
                    or parts[1] not in ("issue", "issuewild", "iodef")):
                return "قالب CAA: «پرچم تگ مقدار» مثل 0 issue letsencrypt.org"
        elif rtype == "TXT":
            if c.startswith('"') and c.endswith('"') and len(c) >= 2:
                c = c[1:-1]
            if not c.strip():
                return "محتوای TXT خالی است"
    except ValueError:
        return "مقدار IP معتبر نیست"
    return None


# ---------- زون‌ها ----------

def available() -> bool:
    try:
        _req("GET", "/servers/localhost")
        return True
    except (DnsUnavailable, DnsError):
        return False


def server_info() -> dict:
    d = _req("GET", "/servers/localhost")
    return {"version": d.get("version", ""), "id": d.get("id", ""),
            "url": settings.DNS_API_URL}


def list_zones() -> list[dict]:
    data = _req("GET", "/servers/localhost/zones")
    out: list[dict] = []
    for z in (data if isinstance(data, list) else []):
        out.append({
            "name": (z.get("name") or "").rstrip("."),
            "kind": z.get("kind"),
            "dnssec": bool(z.get("dnssec")),
            "serial": z.get("serial"),
            "masters": z.get("masters") or [],
        })
    out.sort(key=lambda x: x["name"])
    return out


def get_zone(zone: str) -> dict:
    z = _req("GET", _zone_url(zone))
    zname = (z.get("name") or "").rstrip(".")
    records: list[dict] = []
    for rr in (z.get("rrsets") or []):
        records.append({
            "name": (rr.get("name") or "").rstrip("."),
            "type": rr.get("type"),
            "ttl": rr.get("ttl"),
            "contents": [(rec.get("content") or "") for rec in (rr.get("records") or [])],
            "disabled": bool(rr.get("disabled")),
        })
    records.sort(key=lambda x: (x["name"] != zname, x["name"], x["type"] or ""))
    return {"name": zname, "kind": z.get("kind"), "serial": z.get("serial"),
            "records": records}


def create_zone(name: str, rrsets: list[dict] | None = None,
                kind: str = "Master") -> dict:
    fq = _fqdn(name)
    if not valid_domain(fq.rstrip(".")):
        raise DnsError("قالب نام زون درست نیست (مثال: example.com)", 400)
    if kind not in ("Master", "Native"):
        raise DnsError("kind باید Master یا Native باشد", 400)
    payload = {"name": fq, "kind": kind, "soa_edit_api": "DEFAULT",
               "rrsets": rrsets or []}
    try:
        _req("POST", "/servers/localhost/zones", payload)
    except DnsError as e:
        if e.status == 409:
            raise DnsError("این زون از قبل وجود دارد", 409)
        raise
    return get_zone(fq)


def delete_zone(zone: str) -> None:
    _req("DELETE", _zone_url(zone))


def replace_soa(zone: str, primary_ns: str, hostmaster: str, ttl: int = 3600) -> None:
    """بازنویسی SOA با مقادیر منطقی (سریال را soa_edit_api مدیریت می‌کند)."""
    patch = {"rrsets": [{
        "name": _fqdn(zone), "type": "SOA", "ttl": ttl, "changetype": "REPLACE",
        "records": [{"content": f"{primary_ns} {hostmaster} 0 10800 3600 604800 3600",
                     "disabled": False}]}]}
    _req("PATCH", _zone_url(zone), patch)


# ---------- رکوردها ----------

def upsert_record(zone: str, name: str, rtype: str, content: str,
                  ttl: int | None = None) -> dict:
    """افزودن/جایگزینی کامل یک rrset (نام+نوع). مقدار متن، همان مقدار نهایی است."""
    rtype = (rtype or "").upper().strip()
    if rtype == "SOA":
        raise DnsError("تغییر SOA از مسیر رکوردها مجاز نیست", 400)
    try:
        ttl = max(60, min(int(ttl or settings.DNS_DEFAULT_TTL), 86400 * 7))
    except (TypeError, ValueError):
        raise DnsError("TTL باید عدد باشد (۶۰ ثانیه تا ۷ روز)", 400)
    fq = _record_fqdn(zone, name)
    c = (content or "").strip()
    err = record_error(fq, rtype, c)
    if err:
        raise DnsError(err, 400)
    # پاوردی‌ان‌اس محتوای TXT را با کوتیشن می‌خواهد
    if rtype == "TXT" and not (c.startswith('"') and c.endswith('"') and len(c) >= 2):
        c = f'"{c}"'
    patch = {"rrsets": [{"name": fq, "type": rtype, "ttl": ttl,
                         "changetype": "REPLACE",
                         "records": [{"content": c, "disabled": False}]}]}
    _req("PATCH", _zone_url(zone), patch)
    return get_zone(zone)


def delete_record(zone: str, name: str, rtype: str) -> dict:
    rtype = (rtype or "").upper().strip()
    if rtype == "SOA":
        raise DnsError("حذف SOA مجاز نیست", 400)
    if rtype not in RECORD_TYPES:
        raise DnsError("نوع رکورد پشتیبانی نمی‌شود", 400)
    patch = {"rrsets": [{"name": _record_fqdn(zone, name), "type": rtype,
                         "changetype": "DELETE"}]}
    _req("PATCH", _zone_url(zone), patch)
    return get_zone(zone)


# ---------- سیم‌کشی با بقیهٔ پرستو ----------

def nameservers(db=None) -> list[str]:
    """نیم‌سرورهای عمومی ما — پیش‌فرض: ns1/ns2 روی دامنهٔ پایهٔ فعال."""
    cfg = (settings.DNS_NAMESERVERS or "").strip()
    if cfg:
        return [h.strip().lower().rstrip(".") for h in cfg.split(",") if h.strip()]
    base = domains.base(db).rstrip(".")
    return [f"ns1.{base}", f"ns2.{base}"]


def _rr(name: str, rtype: str, contents: list[str], ttl: int) -> dict:
    return {"name": name, "type": rtype, "ttl": ttl, "changetype": "REPLACE",
            "records": [{"content": c, "disabled": False} for c in contents]}


def provision_zone(name: str, db=None, kind: str | None = None) -> dict:
    """ساخت زون با پیش‌فرض‌های کامل — یک‌کلیکی برای پذیرش دامنهٔ مشتری.

    kind=customer: NS (نیم‌سرورهای ما) + A ریشه و www به IP سرور + SOA منطقی
    kind=base:     همهٔ موارد بالا + ns1/ns2 و panel/git/apps و وایلکارد *.apps
                   (یعنی دامنهٔ خودِ پرستو — پنل و گیت و سایت‌ها بلافاصله زنده می‌شوند)
    اگر kind داده نشود خودش تشخیص می‌دهد: نام == دامنهٔ پایهٔ فعال → base
    """
    base = domains.base(db).rstrip(".")
    zone = _fqdn(name).rstrip(".")
    if zone == base:
        kind = "base"
    kind = kind or "customer"
    if kind not in ("customer", "base"):
        raise DnsError("kind باید customer یا base باشد", 400)
    ip = domains.server_ip(db)
    if not ip:
        raise DnsError("IP عمومی سرور تشخیص داده نشد؛ PARASTOO_PUBLIC_IP را در .env ست کنید", 500)
    ttl = settings.DNS_DEFAULT_TTL
    ns = nameservers(db) or [f"ns1.{base}", f"ns2.{base}"]
    # درس میدانی v0.3.13: پاوردی‌ان‌اس محتوای NS/SOA-MNAME را باید FQDN مطلق
    # (با نقطهٔ پایانی) بگیرد؛ وگرنه «Not in expected format» می‌دهد (آزمون زنده
    # zeolitecenter.ir، 2026-11-05 — اولین provision میدانی).
    ns = [h.rstrip(".") + "." for h in ns]
    ns1 = ns[0]
    rrsets = [
        _rr(_fqdn(zone), "NS", ns, ttl),
        _rr(_fqdn(zone), "A", [ip], ttl),
        _rr(f"www.{zone}.", "A", [ip], ttl),
    ]
    if kind == "base":
        rrsets += [
            _rr(f"ns1.{zone}.", "A", [ip], ttl),
            _rr(f"ns2.{zone}.", "A", [ip], ttl),
            _rr(f"panel.{zone}.", "A", [ip], ttl),
            _rr(f"git.{zone}.", "A", [ip], ttl),
            _rr(f"apps.{zone}.", "A", [ip], ttl),
            _rr(f"*.apps.{zone}.", "A", [ip], ttl),
        ]
    try:
        create_zone(zone, rrsets=rrsets)
    except DnsError as e:
        if e.status == 409:
            raise DnsError("این زون از قبل وجود دارد", 409)
        raise
    replace_soa(zone, ns1, f"hostmaster.{base}.")
    return get_zone(zone)
