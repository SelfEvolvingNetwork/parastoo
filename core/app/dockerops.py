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

def _tag(owner: str, slug: str, ref: str) -> str:
    return f"{settings.REGISTRY_INTERNAL}/{owner}/{slug}:{ref}"


def build_and_push(build_dir: Path, owner: str, slug: str, sha: str,
                   docker_username: str, docker_password: str,
                   log) -> str:
    """بیلد کانتکست در build_dir، push به رجیستری گیتا؛ برمی‌گرداند: تگ کامل ایمیج."""
    c = client()
    image_tag = _tag(owner, slug, (sha or "latest")[:7])
    latest_tag = _tag(owner, slug, "latest")

    log(f"[build] شروع بیلد از {build_dir}")
    stream = c.api.build(
        path=str(build_dir),
        tag=image_tag,
        rm=True,
        labels={"parastoo.slug": slug, "parastoo.owner": owner},
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


# ---------- اجرای سایت‌ها ----------

def _site_labels(slug: str, subdomain: str, port: int) -> dict:
    router = f"parastoo-site-{slug}"
    tls_on = tls_enabled_now()  # پویا — پس از فعال‌سازی HTTPS در ویزارد اعمال می‌شود
    labels = {
        "traefik.enable": "true",
        "parastoo.site": slug,
        f"traefik.http.routers.{router}.rule": f"Host(`{subdomain}`)",
        f"traefik.http.routers.{router}.entrypoints": (
            "web,websecure" if tls_on else "web"
        ),
        f"traefik.http.services.{router}.loadbalancer.server.port": str(port),
    }
    if tls_on:
        # v0.3.33/34 — الگوی ساب‌دامنهٔ بی‌نهایت: در حالت وایلدکارد (زون روی
        # PDNS خودمان) سایت دیگر گواهی اختصاصی نمی‌خواهد؛ هر روتر سایت روی
        # رزولور wild (DNS-01) با همان وایلدکارد `*.apps.<دامنه>` می‌ماند —
        # صدومین سایت هم هیچ سفارش ACME تازه‌ای نمی‌گشاید. وایلدکارد از خود
        # subdomain مشتق می‌شود نه از env — تا حتی بی‌ری‌استارت هسته (پس از
        # تغییر دامنه در ویزارد) همیشه با دامنهٔ فعال هم‌گام بماند.
        # بیرون از حالت وایلدکارد (nip.io/دامنهٔ بیرونی): مسیر قدیمی — HTTP-01
        # به‌ازای هر هاست (چالش DNS-01 آنجا ناممکن است).
        labels[f"traefik.http.routers.{router}.tls"] = "true"
        if wildcard_mode_now():
            labels[f"traefik.http.routers.{router}.tls.certresolver"] = "wild"
            _rest = (subdomain or "").strip().lower().rstrip(".").split(".", 1)
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


def run_site(image: str, slug: str, subdomain: str, port: int, tier: str,
             pull_creds: tuple[str, str] | None = None) -> str:
    """اجرای کانتینر سایت با محدودیت منابع و برچسب‌های ترافیک. برمی‌گرداند: نام کانتینر."""
    c = client()
    name = f"parastoo-site-{slug}"
    # حذف نسخه قبلی در صورت وجود
    remove_site(slug, quiet=True)

    if pull_creds:
        pull_image(image, *pull_creds)

    kwargs = dict(
        name=name,
        detach=True,
        labels=_site_labels(slug, subdomain, port),
        network=settings.APPS_NETWORK,
        restart_policy={"Name": "unless-stopped"},
        **_tier_limits(tier),
    )
    c.containers.run(image, **kwargs)
    return name


def recreate_site(site) -> str:
    """به‌روزرسانی خودکار سایت با ایمیج جدید (مطابق نیازمندی: با هر تغییر، بروزرسانی خودکار)."""
    return run_site(
        image=site.image,
        slug=site.slug,
        subdomain=site.subdomain,
        port=site.internal_port,
        tier=site.tier,
        pull_creds=None,  # ایمیج از قبل روی دیمون محلی موجود است
    )


def remove_site(slug: str, quiet: bool = False) -> None:
    try:
        c = client()
        container = c.containers.get(f"parastoo-site-{slug}")
        container.remove(force=True)
    except (NotFound, DockerUnavailable):
        if not quiet:
            raise
    except Exception:
        if not quiet:
            raise


def stop_site(slug: str) -> None:
    c = client()
    try:
        c.containers.get(f"parastoo-site-{slug}").stop(timeout=10)
    except NotFound:
        pass


def start_site(slug: str) -> None:
    c = client()
    c.containers.get(f"parastoo-site-{slug}").start()


def container_status(slug: str) -> str | None:
    try:
        c = client()
        return c.containers.get(f"parastoo-site-{slug}").status
    except Exception:
        return None


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
