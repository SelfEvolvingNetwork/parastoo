"""پوشه داکر — بیلد ایمیج، push به رجیستری، اجرای کانتینر سایت‌ها.

همه چیز از طریق Docker SDK (docker-py) انجام می‌شود؛ هیچ دستوری در شل زده نمی‌شود.
رجیستری: رجیستری داخلی گیتا روی 127.0.0.1:3000 (داکر به‌صورت پیش‌فرض localhost را
به‌عنوان رجیستری ناامن می‌پذیرد، پس بدون هیچ پیکربندی اضافه کار می‌کند).
"""
import shutil
from pathlib import Path

from .config import settings
from .domains import tls as tls_enabled_now

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
        labels[f"traefik.http.routers.{router}.tls"] = "true"
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
