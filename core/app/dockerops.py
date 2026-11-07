"""پوشه داکر — بیلد ایمیج، push به رجیستری، اجرای کانتینر سایت‌ها.

همه چیز از طریق Docker SDK (docker-py) انجام می‌شود؛ هیچ دستوری در شل زده نمی‌شود.
رجیستری: رجیستری داخلی گیتا روی 127.0.0.1:3000 (داکر به‌صورت پیش‌فرض localhost را
به‌عنوان رجیستری ناامن می‌پذیرد، پس بدون هیچ پیکربندی اضافه کار می‌کند).

خودترمیم گیتا (v0.3.22): ROOT_URL/DOMAIN کانتینر گیتا هنگام ساخت از env نصب
اول می‌ماند؛ با تغییر دامنه، چالش رجیستری داکر (Www-Authenticate realm) به
دامنهٔ کهنه برمی‌گردد و docker login هر بیلد به «404 page not found» می‌خورد
(میدان 2026-11-06: همهٔ بیلدهای سرور واقعی در مرحلهٔ push می‌مردند). گیتا با
حفظ کامل ویژگی‌ها بازساخته می‌شود و .env هاست همگام می‌ماند.
"""
import shutil
from pathlib import Path

from . import domains
from .config import settings
from .domains import tls as tls_enabled_now
from .domains import wildcard_mode as wildcard_mode_now

try:
    import docker
    from docker.errors import APIError, DockerException, ImageNotFound, NotFound
except ImportError:  # برای تست‌های محلی بدون داکر
    docker = None
    class APIError(Exception): ...
    class DockerException(Exception): ...
    class ImageNotFound(Exception): ...
    class NotFound(Exception): ...


class DockerUnavailable(Exception):
    pass


def client():
    if docker is None:
        raise DockerUnavailable("کتابخانه داکر نصب نیست")
    try:
        c = docker.DockerClient(base_url=settings.DOCKER_SOCK)
        c.ping()
        return c
    except Exception as e:
        raise DockerUnavailable(f"داکر در دسترس نیست: {e}") from e


def available() -> bool:
    try:
        client()
        return True
    except DockerUnavailable:
        return False


# ---------- گیتا: exec ----------

def exec_in_gitea(cmd: list[str]) -> tuple[int, str]:
    c = client()
    try:
        container = c.containers.get(settings.GITEA_CONTAINER)
    except NotFound:
        raise DockerUnavailable(f"کانتینر گیتا ({settings.GITEA_CONTAINER}) پیدا نشد")
    res = container.exec_run(cmd, user="git")
    out = res.output.decode(errors="replace") if res.output else ""
    return res.exit_code or 0, out


# ---------- خودترمیم گیتا: هم‌خوان ROOT_URL با دامنهٔ فعال (v0.3.22) ----------

def gitea_root_url() -> str:
    """ROOT_URL درست برای دامنهٔ فعال جاری (منبع حقیقت: domains)."""
    scheme = "https" if tls_enabled_now() else "http"
    return f"{scheme}://{domains.git()}/"


def gitea_stale() -> tuple[bool, str]:
    """(کهنه؟، مقدار فعلی) — env کانتینر گیتا با دامنهٔ فعال مقایسه می‌شود.

    داکر/گیتا در دسترس نیست → (False, "") یعنی کاری نداریم (خودترمیم فقط
    وقتی معنا دارد که هر دو در دسترس‌اند؛ هیچ مسیری را نمی‌بندد).
    """
    try:
        c = client()
        info = c.api.inspect_container(settings.GITEA_CONTAINER)
    except Exception:
        return (False, "")
    env = {}
    for e in info.get("Config", {}).get("Env") or []:
        k, _, v = e.partition("=")
        env[k] = v
    cur = env.get("GITEA__server__ROOT_URL", "")
    dom = env.get("GITEA__server__DOMAIN", "")
    want_url = gitea_root_url()
    want_dom = domains.git()
    return ((cur.rstrip("/") != want_url.rstrip("/") or dom != want_dom), cur)


def recreate_gitea() -> None:
    """بازسازی کانتینر گیتا با ROOT_URL/DOMAIN دامنهٔ فعال.

    همهٔ ویژگی‌ها از inspect کانتینر فعلی حفظ می‌شوند: ایمیج، بقیهٔ env،
    پورت‌ها، والیوم‌ها، شبکه (+alias سرویس)، healthcheck، لیبل‌ها (حتی
    com.docker.compose.* تا compose همچنان صاحبش بماند)، hostname و
    entrypoint/cmd. فقط دو env دامنه و سه لیبل ترافیک گیتا تازه می‌شوند.
    داده‌ها روی volume‌اند؛ حذف کانتینر داده از بین نمی‌برد.
    """
    c = client()
    name = settings.GITEA_CONTAINER
    info = c.api.inspect_container(name)
    cfg = info.get("Config", {})
    hcfg = info.get("HostConfig", {})

    env = [e for e in cfg.get("Env") or []
           if not e.startswith(("GITEA__server__ROOT_URL=",
                                "GITEA__server__DOMAIN="))]
    env.append(f"GITEA__server__ROOT_URL={gitea_root_url()}")
    env.append(f"GITEA__server__DOMAIN={domains.git()}")

    labels = dict(cfg.get("Labels") or {})
    tls_on = tls_enabled_now()
    git_r = "traefik.http.routers.parastoo-git."
    labels[git_r + "rule"] = f"Host(`{domains.git()}`)"
    labels[git_r + "entrypoints"] = "web,websecure" if tls_on else "web"
    labels[git_r + "tls"] = "true" if tls_on else "false"

    binds = hcfg.get("Binds") or []
    port_bindings = hcfg.get("PortBindings") or {}
    restart_policy = hcfg.get("RestartPolicy") or {"Name": "unless-stopped"}
    net_name = hcfg.get("NetworkMode") or ""
    nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
    aliases = [a for a in (next(iter(nets.values()), {}) or {}).get("Aliases") or [] if a]

    host_config = c.api.create_host_config(
        binds=binds,
        port_bindings=port_bindings,
        restart_policy=restart_policy,
        network_mode=net_name or None,
    )
    networking_config = None
    if net_name and aliases:
        networking_config = c.api.create_networking_config({
            net_name: c.api.create_endpoint_config(aliases=aliases)})

    create_kwargs = dict(
        image=cfg.get("Image"),
        name=name,
        environment=env,
        labels=labels,
        host_config=host_config,
        ports=list(port_bindings.keys()),
        healthcheck=cfg.get("Healthcheck"),
        entrypoint=cfg.get("Entrypoint"),
        command=cfg.get("Cmd"),
        working_dir=cfg.get("WorkingDir") or None,
    )
    hostname = cfg.get("Hostname")
    if hostname:
        create_kwargs["hostname"] = hostname
    if networking_config:
        create_kwargs["networking_config"] = networking_config

    # داده‌ها روی والیوم‌اند — حذف کانتینر کهنه و ساخت با همان نام
    c.api.remove_container(name, force=True)
    c.api.create_container(**create_kwargs)
    c.api.start(name)


def update_host_env(pairs: dict[str, str]) -> bool:
    """به‌روزرسانی کلید=مقدار در .env هاست (best-effort؛ هرگز استثنا نمی‌دهد).

    اگر کلید موجود باشد همان‌جا جایگزین می‌شود (همهٔ تکرارها)؛ اگر نباید
    در انتهای فایل اضافه می‌شود. False یعنی فایل سوار نیست/نوشتنی نیست —
    فراخواننده فقط لاگ کند؛ بازسازی گیتا مستقل از این کار درست است.
    """
    p = Path(settings.HOST_ENV_FILE) if getattr(settings, "HOST_ENV_FILE", "") else None
    if not p or not p.is_file():
        return False
    try:
        out = []
        seen = set()
        for ln in p.read_text(encoding="utf-8").splitlines():
            k = ln.split("=", 1)[0].strip()
            if k in pairs:
                out.append(f"{k}={pairs[k]}")
                seen.add(k)
            else:
                out.append(ln)
        for k, v in pairs.items():
            if k not in seen:
                out.append(f"{k}={v}")
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _host_env_get(name: str) -> str:
    """خواندن یک کلید از .env هاست (سوار در HOST_ENV_FILE) — برای مقادیری که
    install.sh می‌نویسد ولی از طریق compose به هسته پاس داده نشده‌اند."""
    p = Path(settings.HOST_ENV_FILE) if getattr(settings, "HOST_ENV_FILE", "") else None
    if not p or not p.is_file():
        return ""
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln.startswith(f"{name}="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _pin_ip() -> str:
    """IP قابل‌دسترس از داخل کانتینرها برای پین نام‌های پلتفرم (DNS خوداتکا v0.4.6).

    اولویت: PARASTOO_DNS_BIND_IP از .env هاست — نصب‌کننده محلی‌بودنش را روی یک
    اینترفیس گارد می‌کند (درس میدانی v0.3.25: IP خروجی NAT نباید جایگزین شود)؛
    سپس PARASTOO_PUBLIC_IP. غیبت هر دو = قابلیت خاموش (توسعه/نصب ناقص).
    ترافیک پورت 80/443 را روی همهٔ اینترفیس‌ها پابلیش می‌کند (compose) پس همین
    IP برای مسیر hairpin کانتینر→ترافیک نیز درست است (میدان v0.4.6: connect 0.2ms).
    """
    return _host_env_get("PARASTOO_DNS_BIND_IP") or settings.PUBLIC_IP or ""


def pin_host_entries() -> dict[str, str]:
    """نقشهٔ نام→IP برای /etc/hosts کانتینرهای پروژه — DNS خوداتکا (v0.4.6).

    تصمیم مالک (2026-11-07): پروژه‌ها با API استاندارد از وب جهانی و از پروژه‌های
    دیگرِ همان سرور قابل دسترس‌اند — تنها درِ مشترک، URL عمومی است. برای آنکه
    فراخوانی شرق-غرب از کانتینر به DNS بالادست وابسته نماند (ریسک SERVFAIL
    دامنه‌های .ir — درس v0.3.35)، نام‌های پلتفرم (زیرساخت + همهٔ سایت‌های
    منتشرشده) در /etc/hosts پین می‌شوند.

    چرا نه dns= دومرحله‌ای [نیم‌سرور خودمان، بالادست]؟ آزمون میدانی v0.4.6:
    forwarder داکر روی شبکهٔ user-defined با پاس REFUSED سرِ اول می‌ماند —
    google.com/pypi.org با EAI_AGAIN شکست خوردند. پین hosts زنجیرهٔ فعلی را
    دست‌نخورده نگه می‌دارد و فقط نام‌های خودمان را از بالادست جدا می‌کند.

    بی‌ضرری ورودی‌های کهنه: نام حذف‌شده → 404 ترافیک، همان رفتار مسیر DNS؛
    و نام‌های منتشرشدهٔ «بعد از» این استقرار، تا deploy بعدی از مسیر DNS
    بالادست می‌آیند (رفتار امروز) — یعنی پین شتاب‌دهنده است، نه پیش‌شرط.
    """
    ip = _pin_ip()
    if not ip:
        return {}
    names: list[str] = []
    for d in (domains.panel(), domains.git(), domains.base(), f"www.{domains.base()}"):
        d = (d or "").strip().lower()
        if d and d not in names:
            names.append(d)
    try:  # سایت‌های منتشرشدهٔ همهٔ پروژه‌ها — منبع حقیقت: جدول sites
        from .db import SessionLocal
        from .models import Site
        s = SessionLocal()
        try:
            for (sub,) in s.query(Site.subdomain).all():
                sub = (sub or "").strip().lower()
                if sub and sub not in names:
                    names.append(sub)
        finally:
            s.close()
    except Exception:
        pass  # DB غایب (توسعه/مهاجرت) — نام‌های زیرساختی کافی است
    return {n: ip for n in names}


def sync_host_env() -> bool:
    """همگام‌سازی دامنه‌های .env هاست با دامنهٔ فعال — تا اجرای بعدی
    docker compose interpolation کهنه را پس نزند (ریشهٔ بازگشت باگ)."""
    git_d = domains.git()
    tls_on = tls_enabled_now()
    return update_host_env({
        "BASE_DOMAIN": domains.base(),
        "PANEL_DOMAIN": domains.panel(),
        "GIT_DOMAIN": git_d,
        "APPS_DOMAIN": domains.apps(),
        "GITEA_ROOT_URL": f"https://{git_d}/" if tls_on else f"http://{git_d}/",
        "TRAEFIK_ENTRYPOINTS": "web,websecure" if tls_on else "web",
        "TRAEFIK_TLS": "true" if tls_on else "false",
    })


# ---------- بیلد و رجیستری ----------

def _tag(owner: str, slug: str, svc: str, ref: str) -> str:
    # رجیستری گیتا مسیرهای تو در تو (owner/slug/svc) را می‌پذیرد
    return f"{settings.REGISTRY_INTERNAL}/{owner}/{slug}/{svc}:{ref}"


def build_and_push(build_dir: Path, owner: str, slug: str, svc: str, sha: str,
                   docker_username: str, docker_password: str,
                   log) -> str:
    """بیلد کانتکست یک سرویس، push به رجیستری گیتا؛ برمی‌گرداند: تگ کامل ایمیج.

    v0.4.0: بیلد به دانهٔ سرویس است — هر سرویس پروژه تگ خودش را می‌گیرد.
    """
    c = client()
    image_tag = _tag(owner, slug, svc, (sha or "latest")[:7])
    latest_tag = _tag(owner, slug, svc, "latest")

    log(f"[build:{svc}] شروع بیلد از {build_dir}")
    stream = c.api.build(
        path=str(build_dir),
        tag=image_tag,
        rm=True,
        labels={"parastoo.slug": slug, "parastoo.owner": owner,
                "parastoo.service": svc},
        decode=True,
    )
    for chunk in stream:
        if "stream" in chunk:
            line = chunk["stream"].strip()
            if line:
                log(line)
        elif "errorDetail" in chunk:
            msg = chunk["errorDetail"].get("message", "build error")
            log(f"[error] {msg}")
            raise DockerUnavailable(f"بیلد ناموفق: {msg}")

    img = c.images.get(image_tag)
    img.tag(latest_tag)

    # (v0.3.22) پیام خوانا برای شکست رجیستری — خطای خام داکر (مثل «500 Server
    # Error … 404 page not found») برای کاربر مبهم است؛ ریشهٔ رایج: realm کهنهٔ
    # گیتا (ROOT_URL نصب اول) که ورکر خودترمیم آن را بازسازی می‌کند.
    try:
        log(f"[registry] ورود به {settings.REGISTRY_INTERNAL}")
        c.login(username=docker_username, password=docker_password,
                registry=settings.REGISTRY_INTERNAL)
        log(f"[registry] push {image_tag}")
        for line in c.images.push(image_tag, stream=True, decode=True):
            if "error" in line:
                raise DockerUnavailable(f"push ناموفق: {str(line)[:200]}")
        for line in c.images.push(latest_tag, stream=True, decode=True):
            if "error" in line:
                raise DockerUnavailable(f"push ناموفق: {str(line)[:200]}")
    except DockerUnavailable:
        raise
    except Exception as e:
        hint = (" رجیستری داخلی به دامنهٔ نصب اول ارجاع می‌دهد (ROOT_URL گیتا کهنه است)؛"
                " ورکر خودترمیم ظرف چند دقیقه گیتا را با دامنهٔ فعال بازسازی می‌کند —"
                " بعد از آن «بیلد مجدد» را بزنید." if "404" in str(e) else "")
        raise DockerUnavailable(
            f"انتشار ایمیج در رجیستری داخلی ناموفق بود: {str(e)[:200]}.{hint}") from e
    log("[done] ایمیج با موفقیت ساخته و منتشر شد")
    return image_tag


def pull_image(image: str, username: str, password: str) -> None:
    """pull ایمیج خصوصی با اعتبار مالکش (سمت سرور؛ کاربر نیاز به رجیستری ندارد)."""
    c = client()
    try:
        c.images.get(image)
        return
    except (ImageNotFound, NotFound):
        pass
    c.login(username=username, password=password,
            registry=settings.REGISTRY_INTERNAL)
    c.images.pull(image)


# ---------- اجرای پروژه‌ها (سرویس‌محور v0.4.0) ----------
#
# هندسهٔ ایزولگی: هر پروژه شبکهٔ خصوصی خودش را دارد (parastoo-proj-<id>) و
# کانتینرهایش فقط همان‌جا زندگی می‌کنند؛ کانتینرهای دو پروژهٔ مختلف به‌ساختاری
# هم را نمی‌بینند. تنها سرویسِ عمومیِ پروژه به شبکهٔ apps هم وصل می‌شود تا
# ترافیک (که روی apps است) به آن برسد. نام سرویس‌ها داخل شبکهٔ خصوصی به‌عنوان
# نام میزبان DNS داکر به هم می‌رسند (مثلاً db:5432 از داخل web).
# مالکیت در نام‌ها و لیبل‌ها نیز صریح است: نام کانتینر parastoo-p<id>-<svc> و
# لیبل parastoo.project=<id> — هر استعلام با فیلتر لیبل به دانهٔ پروژه محکوم است.

PROJECT_LABEL = "parastoo.project"
SERVICE_LABEL = "parastoo.service"


def project_network(pid: int) -> str:
    return f"parastoo-proj-{pid}"


def service_container(pid: int, svc_name: str) -> str:
    return f"parastoo-p{pid}-{svc_name}"


def volume_name(pid: int, vol: str) -> str:
    # volumeهای کاربر namespace می‌گیرند تا هیچ پروژه‌ای volume دیگری را صدا نزند
    return f"parastoo-p{pid}-{vol}"


def _project_filter(pid: int) -> dict:
    return {"label": f"{PROJECT_LABEL}={pid}"}


def ensure_network(pid: int):
    """شبکهٔ خصوصی پروژه — اگر نبود می‌سازد؛ شیء شبکه برمی‌گرداند."""
    c = client()
    name = project_network(pid)
    try:
        return c.networks.get(name)
    except Exception:
        return c.networks.create(
            name, driver="bridge",
            labels={PROJECT_LABEL: str(pid)},
        )


def delete_network(pid: int) -> None:
    try:
        c = client()
        net = c.networks.get(project_network(pid))
        net.remove()
    except Exception:
        pass  # نبودن شبکه = کاری نداریم


def _public_labels(sites, port: int) -> dict:
    """لیبل‌های ترافیک سرویس عمومی — یک روتر به‌ازای هر سایتِ پروژه.

    همان الگوی ساب‌دامنهٔ بی‌نهایت (v0.3.33/34): در حالت وایلدکارد همهٔ
    روترها روی رزولور wild با وایلدکارد `*.apps.<دامنه>` می‌مانند و صدومین
    سایت هم هیچ سفارش ACME تازه‌ای نمی‌گشاید؛ بیرون از وایلدکارد مسیر قدیمی
    HTTP-01 به‌ازای هر هاست.
    """
    tls_on = tls_enabled_now()  # پویا — پس از فعال‌سازی HTTPS در ویزارد اعمال می‌شود
    labels: dict = {}
    # درس میدانی v0.4.5 (سفر کامل پل، دور دوم آزمون زنده): کانتینر سرویس عمومی
    # روی دو شبکه است (خصوصیِ پروژه در create + apps در connectِ پس از start)؛
    # بدون `traefik.docker.network`، ترافیک IP بک‌اند را از شبکهٔ اشتباه برمی‌دارد
    # (مثلاً 172.23.x به‌جای 172.19.x) و سایت سکه‌اندازی ۵۰/۵۰ هنگ می‌کند — با
    # کانتینر سالم و روترِ درست. این لیبل منبع IP را قطعی می‌کند.
    labels["traefik.docker.network"] = settings.APPS_NETWORK
    for site in sites:
        router = f"parastoo-site-{site.slug}"
        labels[f"traefik.http.routers.{router}.rule"] = f"Host(`{site.subdomain}`)"
        labels[f"traefik.http.routers.{router}.entrypoints"] = (
            "web,websecure" if tls_on else "web")
        labels[f"traefik.http.services.{router}.loadbalancer.server.port"] = str(port)
        if tls_on:
            labels[f"traefik.http.routers.{router}.tls"] = "true"
            if wildcard_mode_now():
                labels[f"traefik.http.routers.{router}.tls.certresolver"] = "wild"
                _rest = (site.subdomain or "").strip().lower().rstrip(".").split(".", 1)
                if len(_rest) == 2:
                    labels[f"traefik.http.routers.{router}.tls.domains[0].main"] = (
                        f"*.{_rest[1]}")
            else:
                labels[f"traefik.http.routers.{router}.tls.certresolver"] = "le"
    return labels


def _tier_limits(tier: str) -> dict:
    t = settings.TIERS.get(tier, settings.TIERS["nano"])
    return {
        "mem_limit": f"{t['mem']}m",
        "nano_cpus": int(t["cpus"] * 1e9),
    }


def _pull_if_needed(image: str, pull_creds: tuple[str, str] | None) -> None:
    if pull_creds:
        pull_image(image, *pull_creds)


def deploy_project(project, services, sites,
                   pull_creds: tuple[str, str] | None = None) -> None:
    """استقرار کامل پروژه از روی ردیف‌های Service — دانهٔ پروژه، همیشه از صفر.

    همهٔ کانتینرهای قدیمیِ پروژه (فیلتر لیبل) حذف و همه از نو ساخته می‌شوند:
    اول سرویس‌های غیرعمومی، بعد سرویس عمومی. فراخواننده تضمین می‌کند ایمیج‌ها
    بیلد/موجودند. هر استثنایی بالا می‌آید تا فراخواننده وضعیت را error کند.

    نکتهٔ DNS (میدان v0.4.1): نام کوتاه سرویس (مثلاً db) باید alias شبکهٔ
    خصوصی باشد — نام کامل کانتینر (parastoo-p<id>-db) آن را پوشش نمی‌دهد و
    env کاربر باید بتواند postgres://db:5432 بنویسد.
    """
    c = client()
    pid = project.id
    net_name = project_network(pid)
    ensure_network(pid)
    remove_project(pid, quiet=True)
    # DNS خوداتکا (v0.4.6): یک بار برای همهٔ سرویس‌های پروژه
    hosts_entries = pin_host_entries()

    ordered = sorted(services, key=lambda s: (bool(s.is_public), s.name))
    for svc in ordered:
        if not svc.image:
            continue
        _pull_if_needed(svc.image, pull_creds)
        t = settings.TIERS.get(svc.tier, settings.TIERS["nano"])
        binds = [f"{volume_name(pid, vname)}:{path}" for vname, path in svc.volumes()]
        labels = {
            PROJECT_LABEL: str(pid),
            SERVICE_LABEL: svc.name,
            "parastoo.owner": str(project.owner_id),
            "parastoo.slug": project.slug,
        }
        if svc.is_public:
            # traefik.enable لازم است — docker provider با exposedByDefault:false
            # کانتینرِ بدون این لیبل را کلاً نادیده می‌گیرد (میدان v0.4.0: 404 سایت)
            labels["traefik.enable"] = "true"
            labels.update(_public_labels(sites, svc.port))
        # docker-py قرارداد (دام میدانی v0.4.6): extra_hosts فقط در host_config
        # است — create_container پارامتر hosts ندارد (TypeError در بیلد واقعی)
        host_kwargs: dict = {"extra_hosts": hosts_entries} if hosts_entries else {}
        host_config = c.api.create_host_config(
            binds=binds or None,
            restart_policy={"Name": "unless-stopped"},
            network_mode=net_name,
            mem_limit=t["mem"] * 1024 * 1024,
            nano_cpus=int(t["cpus"] * 1e9),
            **host_kwargs,
        )
        networking_config = c.api.create_networking_config({
            net_name: c.api.create_endpoint_config(aliases=[svc.name]),
        })
        create_kwargs = dict(
            image=svc.image,
            name=service_container(pid, svc.name),
            labels=labels,
            environment=svc.env(),
            host_config=host_config,
            networking_config=networking_config,
            detach=True,
        )
        if svc.command:
            create_kwargs["command"] = ["sh", "-c", svc.command]
        c.api.create_container(**create_kwargs)
        c.api.start(service_container(pid, svc.name))
        if svc.is_public and settings.APPS_NETWORK != net_name:
            try:
                c.networks.get(settings.APPS_NETWORK).connect(
                    service_container(pid, svc.name))
            except Exception:
                pass


def remove_project(pid: int, quiet: bool = False) -> None:
    """حذف همهٔ کانتینرهای پروژه (فیلتر لیبل) — هرگز کانتینر دیگری را نمی‌بیند."""
    c = client()
    try:
        containers = c.containers.list(all=True, filters=_project_filter(pid))
    except DockerUnavailable:
        if quiet:
            return
        raise
    for ctr in containers:
        try:
            ctr.remove(force=True)
        except Exception:
            if not quiet:
                raise


def stop_project(pid: int) -> None:
    c = client()
    for ctr in c.containers.list(all=True, filters=_project_filter(pid)):
        try:
            ctr.stop(timeout=10)
        except Exception:
            pass


def start_project(pid: int) -> None:
    c = client()
    for ctr in c.containers.list(all=True, filters=_project_filter(pid)):
        try:
            ctr.start()
        except Exception:
            pass


def project_container_status(pid: int) -> dict[str, str]:
    """نقشهٔ نام سرویس → وضعیت کانتینر زنده (خالی = داکر در دسترس نیست)."""
    try:
        c = client()
        out = {}
        for ctr in c.containers.list(all=True, filters=_project_filter(pid)):
            svc = (ctr.labels or {}).get(SERVICE_LABEL, ctr.name)
            out[svc] = ctr.status
        return out
    except DockerUnavailable:
        return {}


# ---------- پل عامل: exec/لاگ/ری‌استارت مهارشده (v0.4.3) ----------
#
# قاعدهٔ امنیتی: کشف کانتینر «فقط» با فیلتر لیبل parastoo.project=<id> انجام
# می‌شود؛ نام سرویس ورودی فقط کلید جست‌وجو در همین دانه است. یعنی حتی اگر
# فراخوانِ پل نام هر کانتینر دیگری را بدهد، این لایه عملاً آن را نمی‌بیند.

LOGS_TAIL_MAX = 2000  # سقف خطوط لاگ هر درخواست


def _service_container(pid: int, svc: str):
    """کانتینر سرویسِ یک پروژه — فقط از میان کانتینرهای لیبل‌دار همان پروژه."""
    c = client()
    for ctr in c.containers.list(all=True, filters=_project_filter(pid)):
        if (ctr.labels or {}).get(SERVICE_LABEL) == svc:
            return ctr
    raise DockerUnavailable(f"سرویس «{svc}» برای این پروژه در حال اجرا نیست")


def exec_in_service(pid: int, svc: str, cmd: str,
                    timeout_s: int = 60, max_bytes: int = 256 * 1024) -> dict:
    """اجرا کردن یک دستور داخل کانتینر سرویس (ش -c) — با سقف بایت و تایم‌اوت.

    خروجی استریم خوانده و در سقف max_bytes بسته می‌شود (حافظهٔ هسته هرگز
    با خروجی بی‌کران پر نمی‌شود)؛ کد خروج با poll روی exec_inspect گرفته
    می‌شود. اگر فرایند تا deadline تمام نشود timed_out=True می‌آید؛ فرایند
    ممکن است داخل کانتینر خودِ کاربر به زندگی ادامه دهد (همان اختیار خودش).
    """
    import time as _time

    ctr = _service_container(pid, svc)  # درونش client() را می‌سنجد (DockerUnavailable)
    api = ctr.client.api
    exec_id = api.exec_create(ctr.id, ["sh", "-c", cmd],
                              stdin=False, tty=False)
    t0 = _time.monotonic()
    deadline = t0 + max(1, min(int(timeout_s), 120))
    out: dict = {"exit_code": None, "stdout": "", "stderr": "",
                 "truncated": False, "timed_out": False, "duration_ms": 0}
    so: list[bytes] = []
    se: list[bytes] = []
    got = 0

    def _cap(buf: list[bytes], chunk: bytes) -> bool:
        """الحاق تا سقف؛ False یعنی سقف پر شده و باید خواندن را بست."""
        nonlocal got
        if not chunk:
            return True
        if got >= max_bytes:
            out["truncated"] = True
            return False
        room = max_bytes - got
        if len(chunk) > room:
            buf.append(chunk[:room])
            got += room
            out["truncated"] = True
            return False
        buf.append(chunk)
        got += len(chunk)
        return True

    stream = api.exec_start(exec_id, stream=True, demux=True)
    try:
        for so_chunk, se_chunk in stream:
            ok1 = _cap(so, so_chunk) if so_chunk else True
            ok2 = _cap(se, se_chunk) if se_chunk else True
            if not (ok1 and ok2):
                break
            if _time.monotonic() > deadline:
                out["timed_out"] = True
                break
    except Exception as e:
        out["stderr"] = (out["stderr"] + f"\n[bridge] exec stream error: {type(e).__name__}")[:4000]
    # کد خروج — تا پایان deadline با poll منتظر می‌مانیم (پس از بستن استریم هم
    # دیمون داکر فرایند را پیش می‌برد؛ inspect حقیقت را می‌گوید)
    while _time.monotonic() <= deadline:
        try:
            ins = api.exec_inspect(exec_id)
            if not ins.get("Running", False):
                out["exit_code"] = ins.get("ExitCode")
                break
        except Exception:
            break
        _time.sleep(0.2)
    else:
        out["timed_out"] = True
    out["stdout"] = b"".join(so).decode("utf-8", errors="replace")
    if not out["stderr"]:
        out["stderr"] = b"".join(se).decode("utf-8", errors="replace")
    out["duration_ms"] = int((_time.monotonic() - t0) * 1000)
    return out


def service_logs(pid: int, svc: str, tail: int = 200,
                 max_bytes: int = 100 * 1024) -> str:
    """دمِ لاگ کانتینر سرویس (docker logs) — سقف خط و بایت."""
    ctr = _service_container(pid, svc)  # درونش client() را می‌سنجد
    raw = ctr.logs(tail=str(max(1, min(int(tail), LOGS_TAIL_MAX))), timestamps=True)
    text = raw.decode("utf-8", errors="replace")
    return text[-max_bytes:]


def restart_service(pid: int, svc: str) -> None:
    """ری‌استارت کانتینر یک سرویس — دوباره: فقط در دانهٔ پروژهٔ خودش."""
    ctr = _service_container(pid, svc)  # درونش client() را می‌سنجد
    ctr.restart(timeout=10)


def system_stats() -> dict:
    """آمار کلی برای پنل ادمین."""
    try:
        c = client()
        containers = c.containers.list(all=True)
        return {
            "docker": True,
            "containers": len(containers),
            "images": len(c.images.list()),
        }
    except DockerUnavailable:
        return {"docker": False, "containers": 0, "images": 0}


def cleanup_build_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
