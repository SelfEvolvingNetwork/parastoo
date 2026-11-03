"""تست دودی تحریم‌ها — سازگاری لیست ایمیج‌ها، ادغام آینه‌ها، بیلد آینه‌پذیر.

حالت آفلاین CI: با متغیر محیطی PARASTOO_SMOKE_OFFLINE=1 بخش‌های زندهٔ
شبکه‌ای (پروب آینه‌های ایرانی) رد می‌شوند — برای GitHub Actions.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OFFLINE = bool(os.environ.get("PARASTOO_SMOKE_OFFLINE"))

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name} {extra}")


# ---------- ۱) syntax همهٔ اسکریپت‌های شل ----------
print("— اسکریپت‌ها —")
for sh in ["install.sh", "setup.sh", "bootstrap.sh", "bin/parastoo",
           "scripts/fetch-images.sh", "scripts/check-mirrors.sh", "uninstall.sh"]:
    r = subprocess.run(["bash", "-n", str(ROOT / sh)], capture_output=True, text=True)
    check(f"bash -n {sh}", r.returncode == 0, r.stderr[:120])

r = subprocess.run(["python3", "-m", "py_compile",
                    str(ROOT / "scripts/merge-docker-mirrors.py")],
                   capture_output=True, text=True)
check("py_compile merge-docker-mirrors.py", r.returncode == 0, r.stderr[:120])

# ---------- ۲) deploy/images.txt == ایمیج‌های compose + قالب‌های stacks + CLI ----------
print("— سازگاری لیست ایمیج‌ها —")
declared = {
    l.strip() for l in (ROOT / "deploy/images.txt").read_text().splitlines()
    if l.strip() and not l.strip().startswith("#")
}

compose = (ROOT / "docker-compose.yml").read_text()
compose_images = set(re.findall(r"^\s*image:\s*(\S+)", compose, re.M))
compose_images.discard("parastoo/core:latest")  # ایمیج محلی‌ساخت (build: ./core) — نیازی به Hub ندارد
core_from = set(re.findall(r"^FROM\s+(\S+)", (ROOT / "core/Dockerfile").read_text(), re.M))

sys.path.insert(0, str(ROOT / "core"))
from app import stacks  # noqa: E402

stack_bases = set()
for stack in ("static", "node", "python"):
    stack_bases |= set(re.findall(r"^FROM\s+(\S+)", stacks.dockerfile_for(stack) or "", re.M))

backup_img = re.search(r"docker run --rm .* (\S+) \\\n\s+tar czf",
                       (ROOT / "bin/parastoo").read_text())
cli_image = {backup_img.group(1)} if backup_img else set()

needed = compose_images | core_from | stack_bases | cli_image
missing = needed - declared
check("همهٔ ایمیج‌های لازم در images.txt هستند", not missing, f"مفقود: {missing}")
check("postgre/gitea/traefik پین‌شده‌اند",
      {"postgres:16-alpine", "gitea/gitea:1.23", "traefik:v3.3"} <= declared)
check("امضای backup با alpine پین‌شده", cli_image == {"alpine:3.20"}, f"یافت‌شده: {cli_image}")

# ---------- ۳) install.sh شامل سه لایهٔ ضدتحریم ----------
print("— لایه‌های install.sh —")
inst = (ROOT / "install.sh").read_text()
check("فال‌بک داکر اوبونتو (docker.io + docker-compose-v2)",
      "docker.io" in inst and "docker-compose-v2" in inst and "install_docker_from_ubuntu" in inst)
check("پروب سریع Hub با /v2/ (401)", "registry-1.docker.io/v2/" in inst and "hub_reachable" in inst)
check("ادغام daemon.json با پایتون", "merge-docker-mirrors.py" in inst)
check("تست زنده‌بودن آینه‌ها از خود سرور", "check-mirrors.sh" in inst)
check("بستهٔ آفلاین قبل از منطق آینه (OFFLINE_LOADED)",
      inst.index("OFFLINE_LOADED=0") < inst.index("check-mirrors.sh"))
check("بارگذاری بستهٔ آفلاین", "offline-images/" in inst and "docker load" in inst)
check("تأیید نهایی با pull واقعی alpine:3.20", "timeout 90 docker pull -q alpine:3.20" in inst)
check("فال‌بک تشخیص IP (۴ سرویس)", inst.count("curl -fs4 --max-time 10") >= 3)
check("متغیرهای pip/npm آینه در .env",
      "PARASTOO_PIP_INDEX_URL" in inst and "PARASTOO_NPM_REGISTRY" in inst)
check("لایهٔ ۴: پروب خودکار PyPI و npm از خود سرور",
      "pypi_ok" in inst and "npm_ok" in inst)
check("لایهٔ ۴: آینه‌های کاندید pip/npm",
      "mirrors.arvancloud.ir/pypi" in inst and "registry.npmjs.ir" in inst)
check("لایهٔ ۴: تشخیص خودکار در .env می‌نشیند (اولویت env ← .env ← خودکار)",
      "${DETECTED_PIP_MIRROR:-}" in inst and "${DETECTED_NPM_MIRROR:-}" in inst)

# ---------- ۳ب) mirrors.txt و check-mirrors.sh ----------
print("— آینه‌ها (deploy/mirrors.txt + check-mirrors.sh) —")
mirrors_file = ROOT / "deploy/mirrors.txt"
mirror_lines = [
    l.strip() for l in mirrors_file.read_text().splitlines()
    if l.strip() and not l.strip().startswith("#")
]
check("mirrors.txt حداقل ۳ آینهٔ زنده (تست‌شدهٔ آبان ۱۴۰۵)",
      {"https://docker.arvancloud.ir", "https://docker.novin.cloud", "https://registry.docker.ir"}
      <= set(mirror_lines), str(mirror_lines))
check("آینه‌های مرده از لیست حذف شده‌اند",
      not any("runflare" in m or "iranserver" in m or "dockerme" in m or "manageit" in m
              for m in mirror_lines))
cm = (ROOT / "scripts/check-mirrors.sh").read_text()
check("check-mirrors.sh: endpoint /v2/ + فیلتر کندی + خروجی مرتب‌شده",
      "/v2/" in cm and "MAX_LATENCY" in cm and "sort -n" in cm and "--apply" in cm)
check("check-mirrors.sh همان لیست mirrors.txt را می‌خواند", "deploy/mirrors.txt" in cm)

# ---------- ۳پ) اجرای زندهٔ check-mirrors.sh + جریان --apply (در CI رد می‌شود) ----------
if OFFLINE:
    print("— اجرای زندهٔ check-mirrors.sh — (حالت آفلاین CI: رد شد)")
    check("اجرای زندهٔ آینه‌ها در حالت آفلاین رد شد", True)
else:
    print("— اجرای زندهٔ check-mirrors.sh —")
    try:
        r = subprocess.run(["bash", "scripts/check-mirrors.sh"], capture_output=True, text=True,
                           timeout=180, cwd=str(ROOT))
        alive_out = [l for l in r.stdout.splitlines() if l.startswith("https://")]
        check("زنده: حداقل یک آینهٔ سالم برمی‌گرداند (مرتب بر سرعت)",
              r.returncode == 0 and len(alive_out) >= 1, f"rc={r.returncode} out={r.stdout[:80]}")
    except subprocess.TimeoutExpired:
        check("زنده: حداقل یک آینهٔ سالم برمی‌گرداند", False, "timeout")

    with tempfile.TemporaryDirectory() as td:
        mf = Path(td) / "mirrors.txt"
        mf.write_text("# تست\nhttps://registry.docker.ir\nhttps://dead.invalid.example\n")
        dj = Path(td) / "daemon.json"
        r = subprocess.run(["bash", "scripts/check-mirrors.sh", "--apply", str(dj)],
                           capture_output=True, text=True, timeout=180, cwd=str(ROOT),
                           env={**os.environ, "PARASTOO_MIRRORS_FILE": str(mf)})
        d = json.loads(dj.read_text()) if dj.exists() else {}
        check("--apply: فقط آینهٔ زنده در daemon.json نوشته شد",
              d.get("registry-mirrors") == ["https://registry.docker.ir"], str(d))

# ---------- ۳ت) setup.sh — نصب با یک دستور ----------
print("— setup.sh و bootstrap.sh —")
setup = (ROOT / "setup.sh").read_text()
check("setup.sh: ریشهٔ نصب /opt/parastoo", '/opt/parastoo' in setup)
check("setup.sh: چهار منبع (zip کنار اسکریپت / مسیر / URL / پوشهٔ بازشده)",
      "parastoo*.zip" in setup and "https://" in setup and 'MODE="dir"' in setup)
check("setup.sh: فال‌بک استخراج با python3 (اگر unzip نبود)",
      "python3 -m zipfile -e" in setup)
check("setup.sh: آپدیت امن — حفظ .env/config/backups/offline-images",
      ".env" in setup and "rm -rf \"$INSTALL_ROOT/core\"" in setup)
check("setup.sh: نرمال‌سازی ساختار zip (پوشهٔ داخلی یا ریشه)",
      "find \"$WORK/src\" -maxdepth 2 -name docker-compose.yml" in setup)
check("setup.sh: در پایان install.sh را اجرا می‌کند", "exec bash install.sh" in setup)
check("setup.sh: حالت curl | bash (BASH_SOURCE گارد شده)",
      'BASH_SOURCE[0]:-' in setup)
bs = (ROOT / "bootstrap.sh").read_text()
check("bootstrap.sh: در حالت pipe بسته را خودش باز می‌کند و «مسیر پوشه» را می‌دهد",
      'exec bash "$SRC_DIR/setup.sh" "$SRC_DIR"' in bs and "bash setup.sh \"$WORK" not in bs)
check("bootstrap.sh: سه مسیر دریافت (ریلیز/تاربال/کلون)",
      "releases/latest/download" in bs and "codeload.github.com" in bs and "git clone" in bs)

# ---------- ۴) اجرای زندهٔ merge-docker-mirrors.py: ساخت/ادغام/حفظ/تکرار/JSON خراب ----------
print("— merge-docker-mirrors.py —")
MERGE = str(ROOT / "scripts/merge-docker-mirrors.py")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "daemon.json"

    r = subprocess.run(["python3", MERGE, str(p), "https://a.example/", "https://b.example"],
                       capture_output=True, text=True)
    d = json.loads(p.read_text())
    check("فایل غایب ساخته می‌شود", r.returncode == 0 and d["registry-mirrors"] == [
        "https://a.example", "https://b.example"], str(d))

    p.write_text(json.dumps({"log-level": "INFO", "registry-mirrors": ["https://mine.example"]}))
    r = subprocess.run(["python3", MERGE, str(p), "https://mine.example", "https://new.example"],
                       capture_output=True, text=True)
    d = json.loads(p.read_text())
    check("کلیدهای دیگر حفظ + تکرار حذف + ترتیب کاربر اول",
          d["log-level"] == "INFO" and d["registry-mirrors"] == [
              "https://mine.example", "https://new.example"], str(d))

    p.write_text("{broken json!!")
    r = subprocess.run(["python3", MERGE, str(p), "https://x.example"],
                       capture_output=True, text=True)
    check("JSON خراب → خطا و خروج ۱، فایل دست‌نخورده",
          r.returncode == 1 and "{broken json!!" in p.read_text())

# ---------- ۵) قالب‌های بیلد: آینهٔ pip/npm فقط وقتی تنظیم شده ----------
print("— قالب‌های بیلد آینه‌پذیر —")
env_backup = {k: os.environ.pop(k, None) for k in ("PARASTOO_PIP_INDEX_URL", "PARASTOO_NPM_REGISTRY")}
import importlib  # noqa: E402
from app import config  # noqa: E402
importlib.reload(config)
importlib.reload(stacks)

node_plain = stacks.dockerfile_for("node")
py_plain = stacks.dockerfile_for("python")
check("بدون آینه: npm بدون --registry", "--registry" not in node_plain and "__NPM_OPTS__" not in node_plain)
check("بدون آینه: pip بدون --index-url", "--index-url" not in py_plain and "__PIP_OPTS__" not in py_plain)

os.environ["PARASTOO_NPM_REGISTRY"] = "https://registry.example.ir"
os.environ["PARASTOO_PIP_INDEX_URL"] = "https://pypi.example.ir/simple"
importlib.reload(config)
importlib.reload(stacks)

node_mirror = stacks.dockerfile_for("node")
py_mirror = stacks.dockerfile_for("python")
check("با آینه: npm از رجیستری ایرانی",
      "npm install --omit=dev --registry=https://registry.example.ir" in node_mirror)
check("با آینه: pip از index ایرانی",
      "pip install --no-cache-dir --index-url=https://pypi.example.ir/simple -r requirements.txt" in py_mirror)
check("داکرفایل استاتیک تغییر نمی‌کند", "nginx" in stacks.dockerfile_for("static"))
check("پایه‌های قالب‌ها در images.txt پوشش داده شده‌اند",
      {"node:20-alpine", "python:3.12-slim", "nginx:1.27-alpine"} <= declared)

# پاکسازی env برای تست‌های بعدی
for k, v in env_backup.items():
    if v is not None:
        os.environ[k] = v

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
