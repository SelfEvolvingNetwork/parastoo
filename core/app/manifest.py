"""مانیفست parastoo.yml — تعریف سرویس‌های پروژه + تشخیص خودکار تک‌سرویسی.

معماری سرویس‌محور (v0.4.0): موتور پرستو فقط «سرویس» می‌شناسد و هیچ محدودیت
فناوری ندارد. مخزن به یکی از این دو شکل سرویس‌هایش را اعلام می‌کند:

۱) parastoo.yml در ریشهٔ مخزن — هر تعداد سرویس (تا سقف MAX_SERVICES)؛
   هر سرویس یا image آماده دارد یا build از یک پوشهٔ نسبی. هر ران‌تایمی:
     parastoo: 1
     services:
       web:
         build: .
         port: 3000
         public: true
         env: [DATABASE_URL=postgres://db:5432/app]
       db:
         image: postgres:16-alpine
         volume: dbdata:/var/lib/postgresql/data
 2) مخزن بدون مانیفست — تشخیص خودکار تک‌سرویسی از محتوای ریشه:
    Dockerfile → خودِ آن | package.json → قالب node | requirements.txt →
    قالب python | index.html → قالب static | هیچ‌کدام → خطای گویا.

ایمنی (لیست سفید سخت): کلیدهای ناشناخته، privileged، network_mode، devices،
بایند مسیر هاست، پورت هاست و... رد می‌شوند. شبکهٔ سرویس‌ها خصوصیِ همان پروژه
است؛ نام سرویس‌ها داخل شبکه به‌عنوان نام میزبان (DNS داکر) به هم می‌رسند.
تفسیر متغیر بین سرویس‌ها (${...}) عمداً انجام نمی‌شود — مقادیر لفظی‌اند.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import settings
from .scaffolds import DEFAULT_START_CMD

MANIFEST_NAME = "parastoo.yml"
MAX_MANIFEST_BYTES = 64 * 1024

_SVC_NAME = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VOL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

# کلیدهای مجاز مشخصات هر سرویس — هر چیز دیگری خطای گویا می‌دهد
_ALLOWED_KEYS = {"image", "build", "port", "command", "env", "volumes",
                 "volume", "public", "tier"}

FORBIDDEN_KEYS_HINT = {
    "privileged": "privileged مجاز نیست — کانتینرهای پرستو بدون دسترسی ویژه اجرا می‌شوند.",
    "devices": "devices مجاز نیست.",
    "network_mode": "network_mode مجاز نیست — هر پروژه شبکهٔ خصوصی خودش را دارد.",
    "ports": "ports (پورت هاست) مجاز نیست — فقط سرویس عمومی از طریق HTTPS دامنه در دسترس است.",
    "depends_on": "depends_on لازم نیست — پرستو سرویس‌های غیرعمومی را اول و سرویس عمومی را آخر اجرا می‌کند.",
    "volumes:bind": "بایند مسیر هاست مجاز نیست — فقط volume نام‌دار (name:/path).",
}


class ManifestError(Exception):
    """خطای مانیفست با پیام فارسیِ قابل‌اقدام."""


@dataclass(frozen=True)
class ServiceSpec:
    """نتیجهٔ تجزیه: یک سرویس اعلام‌شدهٔ پروژه (متادیتای خالص، بدون اثر جانبی)."""
    name: str
    image: str | None = None
    build: str | None = None          # مسیر نسبی کانتکست بیلد؛ None = فقط image
    port: int = 3000
    command: str = ""
    env: tuple[tuple[str, str], ...] = ()
    volumes: tuple[tuple[str, str], ...] = ()
    is_public: bool = False
    tier: str = "nano"
    # داکرفایل قالبی پرستو (فقط مسیر تشخیص خودکار؛ برای مانیفست همیشه None است —
    # اگر کانتکست خودش Dockerfile نداشته باشد این قالب نوشته می‌شود)
    dockerfile_template: str | None = None
    # پیش‌بازرسی استک قالبی (مثل package.json برای node) — پیام فارسی stacks
    marker: tuple[str, str] | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "image": self.image, "build": self.build,
            "port": self.port, "command": self.command, "env": dict(self.env),
            "volumes": [list(v) for v in self.volumes],
            "is_public": self.is_public, "tier": self.tier,
        }


# ---------------------------------------------------------------- مانیفست

def _fail(msg: str):
    raise ManifestError(msg)


def _as_str(val, where: str) -> str:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return str(val)
    if not isinstance(val, str):
        _fail(f"{where}: مقدار باید رشته باشد.")
    return val


def _parse_env(raw, where: str) -> tuple[tuple[str, str], ...]:
    """env به دو فرم می‌آید (هر دو سبک compose): نگاشت {KEY: val} یا لیست
    ["KEY=val", ...]. مقادیر لفظی‌اند — هیچ interpolation‌ای انجام نمی‌شود."""
    if raw is None:
        return ()
    items: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            s = _as_str(item, f"{where}.env").strip()
            if "=" not in s:
                _fail(f"{where}: عضو env «{s}» قالب KEY=مقدار ندارد.")
            k, _, v = s.partition("=")
            items.append((k, v))
    elif isinstance(raw, dict):
        items = [(str(k), v) for k, v in raw.items()]
    else:
        _fail(f"{where}: env باید نگاشت KEY=مقدار یا لیست [KEY=مقدار] باشد.")
    out = []
    for k, v in items:
        if not _ENV_KEY.match(k):
            _fail(f"{where}: نام متغیر محیطی «{k}» معتبر نیست (حروف/عدد/زیرخط).")
        v = _as_str(v, f"{where}.env.{k}")
        if len(v) > 2048:
            _fail(f"{where}: مقدار متغیر {k} بیش از حد بزرگ است (سقف ۲۰۴۸ کاراکتر).")
        out.append((k, v))
    if len(out) > 32:
        _fail(f"{where}: بیش از ۳۲ متغیر محیطی مجاز نیست.")
    return tuple(out)


def _parse_volumes(raw, where: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        s = _as_str(item, f"{where}.volumes").strip()
        if ":" not in s or s.startswith("/") or s.startswith(".") or "/" in s.split(":")[0]:
            _fail(f"{where}: قالب volume «{s}» درست نیست؛ نام:/مسیرِ داخل‌کانتینر (نام‌دار، بدون مسیر هاست).")
        name, _, path = s.partition(":")
        if not _VOL_NAME.match(name):
            _fail(f"{where}: نام volume «{name}» باید حروف لاتین کوچک/عدد/خط تیره باشد.")
        if not path.startswith("/") or ".." in Path(path).parts:
            _fail(f"{where}: مسیر volume «{path}» باید مطلق داخل کانتینر باشد (بدون ..).")
        out.append((name, path))
    if len(out) > 8:
        _fail(f"{where}: بیش از ۸ volume به‌ازای هر سرویس مجاز نیست.")
    return tuple(out)


def _parse_service(name: str, spec) -> ServiceSpec:
    if not _SVC_NAME.match(name):
        _fail(f"نام سرویس «{name}» معتبر نیست: حروف لاتین کوچک/عدد/خط تیره، ۲ تا ۳۱ کاراکتر.")
    if not isinstance(spec, dict):
        _fail(f"سرویس «{name}»: مشخصات باید نگاشت باشد.")
    unknown = set(map(str, spec.keys())) - _ALLOWED_KEYS
    if unknown:
        hint = " ".join(FORBIDDEN_KEYS_HINT.get(u, f"«{u}» پشتیبانی نمی‌شود.")
                        for u in sorted(unknown))
        _fail(f"سرویس «{name}»: کلید(های) ناشناخته {sorted(unknown)} — {hint}")
    image = spec.get("image")
    build = spec.get("build")
    if image is not None:
        image = _as_str(image, f"services.{name}.image").strip() or None
        if image and not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./:-]*$", image):
            _fail(f"سرویس «{name}»: نام ایمیج «{image}» معتبر نیست.")
    if build is not None:
        build = _as_str(build, f"services.{name}.build").strip() or "."
        if build.startswith("/") or ".." in Path(build).parts:
            _fail(f"سرویس «{name}»: build باید مسیر نسبی داخل مخزن باشد.")
    if not image and build is None:
        _fail(f"سرویس «{name}»: یا image یا build لازم است.")
    port = spec.get("port", 3000)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        _fail(f"سرویس «{name}»: port باید عدد ۱ تا ۶۵۵۳۵ باشد.")
    command = spec.get("command")
    if isinstance(command, list):
        command = " ".join(_as_str(c, f"services.{name}.command") for c in command)
    elif command is not None:
        command = _as_str(command, f"services.{name}.command")
    else:
        command = ""
    tier = spec.get("tier") or "nano"
    if tier not in settings.TIERS:
        _fail(f"سرویس «{name}»: tier باید یکی از {list(settings.TIERS)} باشد.")
    return ServiceSpec(
        name=name, image=image, build=build, port=port,
        command=command.strip(), env=_parse_env(spec.get("env"), f"services.{name}"),
        volumes=_parse_volumes(spec.get("volumes", spec.get("volume")), f"services.{name}"),
        is_public=bool(spec.get("public")),
        tier=tier,
    )


def parse_manifest(text: str) -> list[ServiceSpec]:
    """پارس امن مانیفست با لیست سفید سخت — خروجی: فهرست ServiceSpec."""
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        _fail("فایل مانیفست بیش از حد بزرگ است (سقف ۶۴KB).")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        _fail(f"مانیفست YAML معتبر نیست: {str(e)[:200]}")
    if not isinstance(doc, dict):
        _fail("مانیفست باید نگاشت باشد و با «parastoo: 1» شروع شود.")
    unknown = set(map(str, doc.keys())) - {"parastoo", "services"}
    if unknown:
        hint = " ".join(FORBIDDEN_KEYS_HINT.get(u, f"«{u}» پشتیبانی نمی‌شود.")
                        for u in sorted(unknown))
        _fail(f"کلیدهای ریشهٔ ناشناخته: {sorted(unknown)} — {hint}")
    ver = doc.get("parastoo")
    if ver not in (1, "1"):
        # الزامی است تا یک docker-compose چسبانده‌شده به‌اشتباه پذیرفته نشود
        _fail("مانیفست باید با «parastoo: 1» شروع شود (نسخهٔ قالب).")
    services = doc.get("services")
    if not isinstance(services, dict) or not services:
        _fail("بخش services خالی است — دست‌کم یک سرویس تعریف کنید.")
    if len(services) > settings.MAX_SERVICES:
        _fail(f"حداکثر {settings.MAX_SERVICES} سرویس به‌ازای هر پروژه مجاز است.")
    specs = [_parse_service(str(n), s) for n, s in services.items()]
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        _fail("نام سرویس‌ها باید یکتا باشند.")
    publics = [s for s in specs if s.is_public]
    if len(publics) > 1:
        _fail("فقط یک سرویس می‌تواند public: true باشد (سرویس عمومی که دامنه می‌گیرد).")
    if not publics:
        if len(specs) == 1:
            # تک‌سرویس بدون اعلام public → خودش عمومی است
            return [ServiceSpec(**{**specs[0].__dict__, "is_public": True})]
        _fail("در حالت چند-سرویسی باید یک سرویس public: true داشته باشید "
              "(سرویسی که زیردامنه به آن روت می‌شود).")
    return specs


def parse_file(repo_dir: Path) -> list[ServiceSpec] | None:
    """مانیفست مخزن را می‌خواند؛ نبود فایل → None (مسیر تشخیص خودکار)."""
    p = repo_dir / MANIFEST_NAME
    if not p.exists():
        return None
    return parse_manifest(p.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- تشخیص خودکار

def detect_stack(repo_dir: Path) -> str:
    """تشخیص نمایشی فناوری از محتوای ریشه (فقط برای پیام/اسکفولد — موتور به آن وابسته نیست)."""
    if (repo_dir / MANIFEST_NAME).exists():
        return "manifest"
    if (repo_dir / "Dockerfile").exists():
        return "docker"
    if (repo_dir / "package.json").exists():
        return "node"
    if (repo_dir / "requirements.txt").exists():
        return "python"
    if (repo_dir / "index.html").exists():
        return "static"
    return "unknown"


def auto_spec(repo_dir: Path) -> list[ServiceSpec]:
    """تک‌سرویس خودکار از محتوای ریشه — مسیر تازه‌کار، بدون هیچ فرم اضافه.

    استک انتخابی در ساخت پروژه اینجا هیچ نقشی ندارد؛ محتوای مخزن حرف می‌زند.
    """
    from . import stacks
    detected = detect_stack(repo_dir)
    if detected == "docker":
        return [ServiceSpec(name="web", build=".", port=3000, is_public=True)]
    if detected == "node":
        return [ServiceSpec(name="web", build=".", port=3000, is_public=True,
                            dockerfile_template=stacks.dockerfile_for("node"),
                            marker=stacks.MARKERS["node"])]
    if detected == "python":
        cmd = DEFAULT_START_CMD["python"]
        return [ServiceSpec(name="web", build=".", port=3000, command=cmd, is_public=True,
                            dockerfile_template=stacks.dockerfile_for("python", cmd),
                            marker=stacks.MARKERS["python"])]
    if detected == "static":
        return [ServiceSpec(name="web", build=".", port=80, is_public=True,
                            dockerfile_template=stacks.dockerfile_for("static"))]
    _fail("در ریشهٔ مخزن نه parastoo.yml هست، نه Dockerfile، نه package.json، "
          "نه requirements.txt، نه index.html. یا یکی از این‌ها را بگذارید یا "
          "با parastoo.yml سرویس‌های پروژه را تعریف کنید (راهنما در صفحهٔ پروژه).")


def resolve(repo_dir: Path) -> tuple[list[ServiceSpec], str]:
    """نقطهٔ ورود موتور بیلد: (سرویس‌ها، نوع منبع manifest|auto)."""
    specs = parse_file(repo_dir)
    if specs is not None:
        return specs, "manifest"
    return auto_spec(repo_dir), "auto"


def specs_to_json(specs: list[ServiceSpec]) -> str:
    """عکس لحظه‌ای سرویس‌ها برای Project.manifest_json — لیست دیکشنری (نه
    رشتهٔ JSON داخل رشته؛ درس میدانی v0.5.1: کدگذاری دوم، خروجی
    /api/bridge/manifest را برای عامل بی‌معنا می‌کرد)."""
    return json.dumps([s.to_dict() for s in specs], ensure_ascii=False)


def json_dumps_env(spec: ServiceSpec) -> str:
    return json.dumps(dict(spec.env), ensure_ascii=False)


def json_dumps_volumes(spec: ServiceSpec) -> str:
    return json.dumps([list(v) for v in spec.volumes])
