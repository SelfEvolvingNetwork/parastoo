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
        r = self.client.post(f"/api/v1/repos/{owner}/{name}", json={
            "name": name, "private": private, "auto_init": False,
            "default_branch": "main", "description": description[:200],
        })
        if r.status_code == 201:
            return r.json()
        if r.status_code == 409:  # از قبل هست
            return {"full_name": f"{owner}/{name}"}
        raise GiteaError(f"create repo {owner}/{name}: {r.status_code} {r.text[:200]}")

    def create_webhook(self, owner: str, repo: str, url: str, secret: str) -> None:
        r = self.client.post(f"/api/v1/repos/{owner}/{repo}/hooks", json={
            "type": "gitea",
            "config": {"url": url, "content_type": "json", "secret": secret},
            "events": ["push"], "active": True,
        })
        if r.status_code not in (201, 409):
            raise GiteaError(f"create webhook for {owner}/{repo}: {r.status_code} {r.text[:200]}")


# ---------- توابع سطح بالای مورد استفادهٔ پرستو ----------

USER_TOKEN_SCOPES = ["write:repository", "write:packages"]
SYSTEM_TOKEN_SCOPES = ["write:repository", "write:packages", "write:user",
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
    gh = GiteaClient(token=token)

    username = user.gitea_username or _unique_username(user.email)
    password = user.gitea_password or secrets.token_urlsafe(18)

    if not gh.user_exists(username):
        gh.admin_create_user(username, user.email, password)
    gtoken = gh.create_token(username, password, "parastoo", USER_TOKEN_SCOPES)

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


def clone_url_internal(owner) -> str:
    """آدرس کلون داخلی برای بیلد (بدون افشای توکن)."""
    return f"http://{owner.gitea_username}:{owner.gitea_token}@gitea:3000/{owner.gitea_username}"
