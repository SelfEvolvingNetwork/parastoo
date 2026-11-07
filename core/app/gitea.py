"""کلاینت API گیتا — همه چیز با HTTP API رسمی Gitea انجام می‌شود (بدون ssh)."""
import secrets

import httpx

from .config import settings


class GiteaError(Exception):
    pass


class GiteaClient:
    """کلاینت همگام برای API داخلی گیتا."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 basic: tuple[str, str] | None = None):
        self.base = (base_url or settings.GITEA_INTERNAL_URL).rstrip("/")
        headers = {}
        if token:
            headers["Authorization"] = f"token {token}"
        auth = basic
        self.client = httpx.Client(base_url=self.base, headers=headers, auth=auth, timeout=15)

    # ---------- سلامت ----------

    def healthy(self) -> bool:
        try:
            r = self.client.get("/api/healthz", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    # ---------- کاربران ----------

    def admin_create_user(self, username: str, email: str, password: str) -> None:
        r = self.client.post("/api/v1/admin/users", json={
            "username": username, "email": email, "password": password,
            "must_change_password": False,
        })
        if r.status_code not in (201, 422):  # 422 = از قبل وجود دارد
            raise GiteaError(f"create user {username}: {r.status_code} {r.text[:200]}")

    def user_exists(self, username: str) -> bool:
        r = self.client.get(f"/api/v1/users/{username}")
        return r.status_code == 200

    def create_token(self, username: str, password: str, name: str,
                     scopes: list[str]) -> str:
        """ساخت توکن دسترسی برای کاربر (با basic auth خود کاربر یا ادمین)."""
        r = self.client.post(
            f"/api/v1/users/{username}/tokens",
            json={"name": name, "scopes": scopes},
        )
        if r.status_code == 201:
            return r.json()["sha1"]
        # درفت نام اسکوپ بین نسخه‌های گیتا: اول جمع→مفرد نرمال‌سازی، بعد حداقلی
        if r.status_code == 400 and "scope" in r.text.lower():
            for alt in (
                [s.replace("write:packages", "write:package")
                   .replace("read:packages", "read:package") for s in scopes],
                ["write:repository"],
            ):
                if alt == scopes:
                    continue
                r = self.client.post(
                    f"/api/v1/users/{username}/tokens",
                    json={"name": name, "scopes": alt},
                )
                if r.status_code == 201:
                    return r.json()["sha1"]
        # توکن تکراری: حذف و ساخت مجدد
        if r.status_code in (422, 500):
            self._delete_token_by_name(username, name)
            r = self.client.post(
                f"/api/v1/users/{username}/tokens",
                json={"name": name, "scopes": scopes},
            )
            if r.status_code == 201:
                return r.json()["sha1"]
        raise GiteaError(f"create token for {username}: {r.status_code} {r.text[:200]}")

    def _delete_token_by_name(self, username: str, name: str) -> None:
        r = self.client.get(f"/api/v1/users/{username}/tokens")
        if r.status_code != 200:
            return
        for t in r.json():
            if t.get("name") == name:
                self.client.delete(f"/api/v1/users/{username}/tokens/{t['id']}")

    # ---------- مخزن ----------

    def create_repo(self, owner: str, name: str, private: bool = True,
                    description: str = "") -> dict:
        payload = {"name": name, "private": private, "auto_init": False,
                   "default_branch": "main", "description": description[:200]}
        # مسیر درست برای ساخت مخزن در نام‌اسپیس «کاربر دیگر» با توکن ادمین:
        # POST /api/v1/admin/users/{owner}/repos — مسیر قدیمی
        # (POST /repos/{owner}/{repo}) در گیتا وجود ندارد و 405 می‌گیرد
        # (حادثهٔ 2026-11-04: هیچ مخزنی ساخته نمی‌شد و پروژه‌ها ابد pending بودند).
        r = self.client.post(f"/api/v1/admin/users/{owner}/repos", json=payload)
        if r.status_code in (403, 404, 405):
            # توکن ادمین نیست → مخزن را در نام‌اسپیس کاربرِ احرازشده بساز
            r = self.client.post("/api/v1/user/repos", json=payload)
        if r.status_code == 201:
            return r.json()
        if r.status_code == 409:  # از قبل هست
            return {"full_name": f"{owner}/{name}"}
        raise GiteaError(f"create repo {owner}/{name}: {r.status_code} {r.text[:200]}")

    def delete_repo(self, owner: str, name: str) -> None:
        r = self.client.delete(f"/api/v1/repos/{owner}/{name}")
        if r.status_code in (200, 204, 404):  # 404 = از قبل نیست؛ همین کافی است
            return
        raise GiteaError(f"delete repo {owner}/{name}: {r.status_code} {r.text[:200]}")

    def repo_info(self, owner: str, repo: str) -> dict | None:
        r = self.client.get(f"/api/v1/repos/{owner}/{repo}")
        return r.json() if r.status_code == 200 else None

    def seed_files(self, owner: str, repo: str, files: dict[str, str], message: str) -> None:
        """کامیت اولیه: چند فایل هم‌زمان در یک کامیت (endpoint تغییر گروهی گیتا 1.20+).

        اگر راه گروهی نشد، فالبک: ساخت تک‌تک فایل‌ها — روی مخزن خالی این هم
        برنچ پیش‌فرض را مقداردهی می‌کند (همان کاری که web UI گیتا می‌کند).
        """
        import base64

        def b64(text: str) -> str:
            return base64.b64encode(text.encode()).decode()

        r = self.client.post(
            f"/api/v1/repos/{owner}/{repo}/contents",
            json={"message": message,  # (v0.3.22) بدون این، گیتا پیام پیش‌فرض انگلیسی «Add …» می‌گذارد
                  "files": [{"operation": "create", "path": p, "content": b64(c)}
                            for p, c in sorted(files.items())]},
        )
        if r.status_code in (200, 201):
            return
        for p, c in sorted(files.items()):
            rp = self.client.post(
                f"/api/v1/repos/{owner}/{repo}/contents/{p}",
                json={"content": b64(c), "message": message},
            )
            if rp.status_code not in (200, 201, 409):  # 409 = از قبل هست؛ اوکی
                raise GiteaError(f"seed file {owner}/{repo}/{p}: {rp.status_code} {rp.text[:200]}")

    def create_webhook(self, owner: str, repo: str, url: str, secret: str) -> None:
        r = self.client.post(f"/api/v1/repos/{owner}/{repo}/hooks", json={
            "type": "gitea",
            "config": {"url": url, "content_type": "json", "secret": secret},
            "events": ["push"], "active": True,
        })
        if r.status_code not in (201, 409):
            raise GiteaError(f"create webhook for {owner}/{repo}: {r.status_code} {r.text[:200]}")


# ---------- توابع سطح بالای مورد استفادهٔ پرستو ----------

# گیتا 1.23+: نام اسکوپ پکیج مفرد است (write:package) — جمعِ آن (write:packages)
# با 400 «invalid access token scope» رد می‌شد و کل زنجیرهٔ توکن→مخزن→بیلد مرده
# می‌ماند (حادثهٔ 2026-11-04: هیچ حساب گیت/مخزنی ساخته نمی‌شد).
USER_TOKEN_SCOPES = ["write:repository", "write:package"]
SYSTEM_TOKEN_SCOPES = ["write:repository", "write:package", "write:user",
                       "write:organization", "write:admin", "read:user"]


def bootstrap_system_admin() -> None:
    """ساخت کاربر مدیر سیستم گیتا (از طریق exec داخل کانتینر گیتا).

    در اولین اجرا فراخوانی می‌شود؛ اگر کاربر موجود باشد بی‌صدا رد می‌شود.
    """
    if not settings.GITEA_ADMIN_PASSWORD:
        raise GiteaError("GITEA_ADMIN_PASSWORD تنظیم نشده است")
    from . import dockerops
    cmd = [
        "gitea", "admin", "user", "create",
        "--admin",
        "--username", settings.GITEA_ADMIN_USER,
        "--email", settings.GITEA_ADMIN_EMAIL,
        "--password", settings.GITEA_ADMIN_PASSWORD,
        "--must-change-password=false",
    ]
    code, out = dockerops.exec_in_gitea(cmd)
    if code != 0 and "already exists" not in out.lower():
        raise GiteaError(f"gitea admin create: {out[:300]}")


def get_system_token() -> str:
    """گرفتن/ساخت توکن سیستمی برای کاربر ادمین گیتا (ذخیره در جدول settings)."""
    from .db import SessionLocal
    from .models import Setting

    basic = (settings.GITEA_ADMIN_USER, settings.GITEA_ADMIN_PASSWORD)
    gh = GiteaClient(basic=basic)
    token = gh.create_token(settings.GITEA_ADMIN_USER, settings.GITEA_ADMIN_PASSWORD,
                            "parastoo-core", SYSTEM_TOKEN_SCOPES)
    db = SessionLocal()
    try:
        row = db.get(Setting, "gitea_system_token")
        if row is None:
            row = Setting(key="gitea_system_token", value=token)
            db.add(row)
        else:
            row.value = token
        db.commit()
    finally:
        db.close()
    return token


def system_token(db=None) -> str:
    """خواندن توکن سیستمی ذخیره‌شده."""
    from .models import Setting
    if db is not None:
        row = db.get(Setting, "gitea_system_token")
        return row.value if row else ""
    from .db import SessionLocal
    db = SessionLocal()
    try:
        row = db.get(Setting, "gitea_system_token")
        return row.value if row else ""
    finally:
        db.close()


def provision_user_git(user) -> None:
    """ساخت حساب گیت + توکن برای کاربر پرستو (idempotent)."""
    if user.gitea_username and user.gitea_token:
        return
    token = system_token()
    if not token:
        raise GiteaError("توکن سیستمی گیتا آماده نیست")
    admin_gh = GiteaClient(token=token)

    username = user.gitea_username or _unique_username(user.email)
    password = user.gitea_password or secrets.token_urlsafe(18)

    if admin_gh.user_exists(username):
        if not user.gitea_password:
            # هم‌نامی در گیتا هست که رمزش را نداریم (بقایای تلاش‌های ناموفق قبل) → نام تازه می‌سازیم
            username = _unique_username(user.email)
            password = secrets.token_urlsafe(18)
            admin_gh.admin_create_user(username, user.email, password)
    else:
        admin_gh.admin_create_user(username, user.email, password)

    # نکتهٔ حیاتی: endpoint ساخت توکن در API گیتا فقط با basic auth «خود کاربر» پاسخ می‌دهد؛
    # ارسال آن با توکن سیستمی 401 می‌گیرد و حساب گیت کاربر هیچ‌وقت کامل نمی‌شود
    # (علامت آن روی پنل: پروژه‌ها برای همیشه «در حال ساخت مخزن» می‌مانند).
    user_gh = GiteaClient(basic=(username, password))
    gtoken = user_gh.create_token(username, password, "parastoo", USER_TOKEN_SCOPES)

    user.gitea_username = username
    user.gitea_password = password
    user.gitea_token = gtoken


def _unique_username(email: str) -> str:
    from .security import slugify
    base = slugify(email.split("@")[0], 30) or "user"
    while len(base) < 4:
        base += "0"
    return f"{base}{secrets.randbelow(9000) + 1000}"


def provision_project_repo(project, owner) -> None:
    """ساخت مخزن گیت + وب‌هوک برای پروژه."""
    token = system_token()
    if not token:
        raise GiteaError("توکن سیستمی گیتا آماده نیست")
    gh = GiteaClient(token=token)
    owner_name = owner.gitea_username
    if not owner_name:
        raise GiteaError("حساب گیت مالک پروژه آماده نیست")

    gh.create_repo(owner_name, project.slug, private=True, description=project.name)
    hook_url = f"http://parastoo-core:8000/api/hooks/gitea/{project.id}"
    gh.create_webhook(owner_name, project.slug, hook_url, project.webhook_secret)


def seed_project_scaffold(project, owner, files: dict[str, str], message: str) -> bool:
    """کامیت اولیهٔ بذر جهانی روی مخزن «خالی».

    قواعد ایمنی (از scaffolds.py):
    - فقط مخزن خالی: اگر محتوا دارد (کاربر زودتر push کرده) هرگز دست نمی‌زنیم.
    - خطاها به بالا می‌رود تا فراخواننده best-effort تصمیم بگیرد (لاگ، بدون شکستن).
    برمی‌گرداند: True = کامیت اولیه اعمال شد.
    """
    token = system_token()
    owner_name = owner.gitea_username
    if not token or not owner_name or not files:
        return False
    gh = GiteaClient(token=token)
    info = gh.repo_info(owner_name, project.slug)
    if info is None or not info.get("empty", True):
        return False  # مخزن نیست یا از قبل محتوا دارد — بازنویسی ممنوع
    gh.seed_files(owner_name, project.slug, files, message)
    return True


def delete_project_repo(project, owner) -> bool:
    """حذف مخزن گیت پروژه (best-effort). False یعنی گیتا در دسترس نبود/ناموفق —
    فراخواننده بدون این هم باید بقیهٔ پاکسازی را انجام دهد."""
    token = system_token()
    owner_name = owner.gitea_username
    if not token or not owner_name:
        return False
    try:
        GiteaClient(token=token).delete_repo(owner_name, project.slug)
        return True
    except Exception:
        return False


def clone_url_internal(owner) -> str:
    """آدرس کلون داخلی برای بیلد (بدون افشای توکن)."""
    return f"http://{owner.gitea_username}:{owner.gitea_token}@gitea:3000/{owner.gitea_username}"
