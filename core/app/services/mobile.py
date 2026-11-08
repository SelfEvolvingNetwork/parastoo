"""منتشرکنندهٔ خودکار بیلدهای «پرستو موبایل» — v0.5.9.

کانال انتشار سرتاسری:
  push سبز به main مخزن parastoo-mobile
    → GitHub Actions بیلد APK می‌گیرد و رلیز گیت‌هاب می‌سازد
      (تگ متحرک mobile-r<run_number>، asset با نام ثابت parastoo-mobile-debug.apk)
    → این سرویس (ورکر هر ۶۰۰ ثانیه) آخرین رلیز را از API عمومی گیت‌هاب
      می‌پرسد؛ اگر تگ تازه‌تری از نصبِ روی دیسک باشد، APK را دانلود،
      راستی‌آزمایی (حجم + امضای ZIP) و به‌صورت اتمیک جایگزین می‌کند
    → صفحهٔ /download پنل همان را با لینک مستقیم نشان می‌دهد.

اصول:
- صفر رمز: مخزن و API عمومی‌اند؛ هیچ توکنی در این مسیر نیست.
- بهترین‌تلاش: شکست شبکه فقط لاگ می‌شود و هرگز چرخهٔ پنل/ورکر را نمی‌بندد.
- اتمی: APK و manifest.json هر دو با نوشتن در فایل موقتِ همان پوشه و
  os.replace نصب می‌شوند — هرگز APK نیمه‌دانلود سرو نمی‌شود.
- سقف دانلود: اگر asset بزرگ‌تر از MAX_APK_BYTES باشد رد می‌شود (گارد ضد
  ریسورس). فایل موقت پیش از شروع دانلود حذف می‌شود.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import settings

REPO_API = ("https://api.github.com/repos/"
            "SelfEvolvingNetwork/parastoo-mobile/releases/latest")
ASSET_NAME = "parastoo-mobile-debug.apk"
# فال‌بک تحریم (SANCTIONS.md): اگر دانلود مستقیم از githubusercontent از سرور
# نگذشت، همان URL از پروکسی دانلود شناخته‌شدهٔ پروژه امتحان می‌شود.
DOWNLOAD_MIRRORS = ("", "https://gh-proxy.com/")
MAX_APK_BYTES = 200 * 1024 * 1024
HTTP_TIMEOUT = 30

STATE_DIR = Path(settings.DATA_DIR) / "mobile"
APK_FILENAME = "parastoo-mobile.apk"
MANIFEST_FILENAME = "manifest.json"

_UA = {"User-Agent": "parastoo-panel-release-sync",  # گیت‌هاب بدون UA جواب نمی‌دهد
       "Accept": "application/vnd.github+json"}


def apk_path() -> Path | None:
    """مسیر APK نصب‌شده — None یعنی هنوز بیلدی منتشر نشده."""
    p = STATE_DIR / APK_FILENAME
    return p if p.is_file() and p.stat().st_size > 0 else None


def read_manifest() -> dict | None:
    try:
        return json.loads((STATE_DIR / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except Exception:
        return None


def status() -> dict:
    """context صفحهٔ /download — همیشه امن برای رندر."""
    m = read_manifest()
    return {
        "mobile": m,
        "mobile_ready": apk_path() is not None and m is not None,
        "apk_url": f"/download/{APK_FILENAME}",
    }


def sync_once() -> str | None:
    """یک دور چک رلیز. خروجی: توضیح یک‌خطی برای لاگ ورکر (None = کاری نشد).

    - تگ همان نصب فعلی → None (بی‌صدا)
    - رلیز بدون APK → None (بیلد هنوز کامل نشده؛ سیکل بعدی)
    - خطای شبکه/API → None با لاگ (سیکل بعدی دوباره تلاش می‌کند)
    """
    info = _latest_release()
    if not info:
        return None
    current = read_manifest()
    if current and current.get("tag") == info["tag"] \
            and current.get("asset_id") == info["asset_id"]:
        return None
    manifest = _download(info)
    return _summary(manifest)


# ---------------------------------------------------------------- internal

def _summary(m: dict) -> str:
    """لاگ نصب — فقط از manifest (رگرسیون 0.5.9: خواندن sha256 از info که نداشت)."""
    return (f"installed {m['tag']} "
            f"({m['size'] / (1024 * 1024):.1f} MB, "
            f"sha256 {m['sha256'][:12]}…)")

def _latest_release() -> dict | None:
    """آخرین رلیز پایدار مخزن موبایل + مشخصات asset APK آن."""
    try:
        req = urllib.request.Request(REPO_API, headers=_UA)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            rel = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # هنوز هیچ رلیزی منتشر نشده — حالت عادی، بی‌صدا
        print(f"[parastoo] mobile release check failed: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[parastoo] mobile release check failed: {e}")
        return None
    if not isinstance(rel, dict) or "tag_name" not in rel:
        return None
    for a in rel.get("assets") or []:
        if a.get("name") == ASSET_NAME:
            return {
                "tag": rel["tag_name"],
                "published_at": rel.get("published_at") or "",
                "asset_id": a.get("id"),
                "size": a.get("size") or 0,
                "url": a.get("browser_download_url") or "",
            }
    return None  # رلیز بدون APK (بیلد ناتمام) — سیکل بعدی


def _download(info: dict) -> dict:
    """دانلود APK با فال‌بک آینه + راستی‌آزمایی + نصب اتمیک. خروجی: manifest ثبت‌شده."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_apk = STATE_DIR / (APK_FILENAME + ".tmp")
    if tmp_apk.exists():
        tmp_apk.unlink()
    expected = info.get("size") or 0

    last_err: Exception | None = None
    for mirror in DOWNLOAD_MIRRORS:
        url = mirror + info["url"]
        if not url:
            continue
        try:
            sha, size = _stream_to(url, tmp_apk)
            break
        except Exception as e:  # آینهٔ بعدی
            last_err = e
            print(f"[parastoo] mobile download failed ({'direct' if not mirror else 'mirror'}): {e}")
            if tmp_apk.exists():
                tmp_apk.unlink()
    else:
        raise RuntimeError(f"mobile APK download failed on all mirrors: {last_err}")

    # راستی‌آزمایی: حجم اعلامی گیت‌هاب + امضای ZIP (APK همیشه PK\x03\x04 است)
    if expected and size != expected:
        tmp_apk.unlink(missing_ok=True)
        raise RuntimeError(f"mobile APK size mismatch: got {size}, expected {expected}")
    if size > MAX_APK_BYTES or not _is_zip(tmp_apk):
        tmp_apk.unlink(missing_ok=True)
        raise RuntimeError("mobile APK failed integrity sanity check")

    os.replace(tmp_apk, STATE_DIR / APK_FILENAME)  # اتمیک روی همان فایل‌سیستم
    manifest = {
        "tag": info["tag"],
        "published_at": info["published_at"],
        "asset_id": info["asset_id"],
        "size": size,
        "sha256": sha,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_url": info["url"],
    }
    tmp_m = STATE_DIR / (MANIFEST_FILENAME + ".tmp")
    tmp_m.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp_m, STATE_DIR / MANIFEST_FILENAME)
    return manifest


def _stream_to(url: str, dest: Path) -> tuple[str, int]:
    """دانلود جریانی با محاسبهٔ هم‌زمان sha256 — خروجی (هش، حجم)."""
    req = urllib.request.Request(url, headers=_UA)
    sha = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp, \
            open(dest, "wb") as fh:
        while chunk := resp.read(256 * 1024):
            size += len(chunk)
            if size > MAX_APK_BYTES:
                raise RuntimeError("download exceeded MAX_APK_BYTES")
            sha.update(chunk)
            fh.write(chunk)
    return sha.hexdigest(), size


def _is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except Exception:
        return False
