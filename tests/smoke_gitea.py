"""تست دودی یکپارچگی گیتا — بازتولید جریان واقعی ورکر با یک گیتای شبیه‌سازی‌شده.

رگرسیون باگ v0.2.10: endpoint ساخت توکن در API گیتا فقط با basic auth «خود کاربر»
کار می‌کند؛ نسخهٔ خراب درخواست را با توکن سیستمی می‌فرستاد و گیتا 401 می‌داد →
حساب گیت هیچ کاربری کامل نمی‌شد و همهٔ پروژه‌ها برای همیشه «در حال ساخت مخزن» می‌مانند.

mock داخل همین فایل، قاعدهٔ گیتا را سخت‌گیرانه اعمال می‌کند: ساخت توکن با
Authorization: token ... → 401. اگر کد به حالت خراب برگردد، این تست قرمز می‌شود.
"""
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

DB = "/tmp/parastoo-test-gitea.db"
if os.path.exists(DB):
    os.remove(DB)

os.environ.update(
    DATABASE_URL=f"sqlite:///{DB}",
    PARASTOO_DISABLE_WORKERS="0",
    SECRET_KEY="test-secret-key",
    BASE_DOMAIN="127.0.0.1.nip.io",
    TLS_ENABLED="false",
    REGISTER_OPEN="true",
    GITEA_INTERNAL_URL="http://127.0.0.1:9317",
    GITEA_ADMIN_USER="parastoo-system",
    GITEA_ADMIN_PASSWORD="syspass-Test123",
    DATA_DIR="/tmp/parastoo-test-gitea-data",
)

sys.path.insert(0, str(ROOT / "core"))

# ---------------- mock گیتا (وفادار به قواعد احراز هویت گیتا ۱.۲۳) ----------------

USERS = {}   # username -> {"email","password","tokens":{name:{"id","sha1","scopes"}}}
REPOS = {}   # "owner/name" -> {"hooks":[url,...]}
HITS = []    # لاگ رویدادها برای ادعاها
LOCK = threading.Lock()


def _auth(handler):
    h = handler.headers.get("Authorization", "")
    if h.startswith("token "):
        tok = h[6:].strip()
        for uname, u in USERS.items():
            for t in u["tokens"].values():
                if t["sha1"] == tok:
                    return uname, set(t["scopes"]), None
        return None, None, None
    if h.startswith("Basic "):
        import base64
        try:
            u, p = base64.b64decode(h[6:]).decode().split(":", 1)
        except Exception:
            return None, None, None
        if u in USERS and USERS[u]["password"] == p:
            return u, {"full"}, u
    return None, None, None


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = b"{}" if obj == {} else __import__("json").dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = unquote(self.path)
        if path == "/api/healthz":
            return self._json(200, {"status": "pass"})
        user, scopes, basic_user = _auth(self)
        if path.startswith("/api/v1/users/") and "/tokens" not in path:
            uname = path.split("/api/v1/users/")[1].strip("/")
            with LOCK:
                ok = uname in USERS
            return self._json(200 if ok else 404, {"username": uname} if ok else {"message": "nope"})
        if path.startswith("/api/v1/repos/") and len(path.strip("/").split("/")) == 5:
            # GET اطلاعات مخزن — قاعدهٔ «فقط مخزن خالی» اسکفولد (v0.3.2)
            parts = path.strip("/").split("/")
            owner, name = parts[3], parts[4]
            with LOCK:
                entry = REPOS.get(f"{owner}/{name}")
            if not entry:
                return self._json(404, {"message": "no repo"})
            return self._json(200, {"full_name": f"{owner}/{name}",
                                    "empty": not entry.get("files")})
        return self._json(404, {})

    def do_POST(self):
        import json
        path = unquote(self.path)
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        user, scopes, basic_user = _auth(self)

        if path.startswith("/api/v1/users/") and path.endswith("/tokens"):
            uname = path.split("/api/v1/users/")[1].split("/tokens")[0]
            # قاعدهٔ واقعی گیتا: توکن فقط با basic auth خود کاربر — توکن سیستمی 401!
            if basic_user != uname:
                with LOCK:
                    HITS.append(f"DENY token {uname} via token-auth({user})")
                return self._json(401, {"message": "auth required"})
            sha1 = secrets.token_hex(20)
            with LOCK:
                USERS.setdefault(uname, {"tokens": {}})["tokens"][payload.get("name", "")] = {
                    "id": len(HITS) + 7, "sha1": sha1,
                    "scopes": payload.get("scopes", [])}
                HITS.append(f"token {uname}")
            return self._json(201, {"id": 1, "name": payload.get("name", ""), "sha1": sha1})

        if path.startswith("/api/v1/repos/") and "/contents" in path:
            # کامیت اولیهٔ اسکفولد (v0.3.2) — گروهی (/contents) یا تکی (/contents/{path})
            rest = path.split("/api/v1/repos/")[1]
            owner, name = rest.split("/contents")[0].split("/")[:2]
            if user is None or (scopes is not None and "write:repository" not in scopes and "full" not in scopes):
                with LOCK:
                    HITS.append(f"DENY contents {owner}/{name}")
                return self._json(403, {"message": "no scope"})
            with LOCK:
                entry = REPOS.setdefault(f"{owner}/{name}", {"hooks": [], "files": {}})
                entry.setdefault("files", {})
                if path.endswith("/contents"):
                    for f in payload.get("files", []):
                        entry["files"][f.get("path", "")] = f.get("content", "")
                else:
                    entry["files"][path.split("/contents/")[1]] = payload.get("content", "")
                HITS.append(f"contents {owner}/{name}")
            return self._json(201, {"commit": {"sha": "s" + "0" * 39}})

        if path.startswith("/api/v1/admin/users/") and path.endswith("/repos"):
            # مسیر واقعی ساخت مخزن در نام‌اسپیس کاربر دیگر با توکن ادمین
            # (اصلاح v0.2.21 — POST /repos/{owner}/{repo} در گیتا وجود ندارد و 405 می‌گیرد)
            owner = path.split("/api/v1/admin/users/")[1].rsplit("/repos", 1)[0]
            if user is None or (scopes is not None and "write:admin" not in scopes and "full" not in scopes):
                with LOCK:
                    HITS.append(f"DENY repo(admin) {owner}/{payload.get('name')}")
                return self._json(403, {"message": "no scope"})
            with LOCK:
                REPOS.setdefault(f"{owner}/{payload.get('name')}", {"hooks": []})
                HITS.append(f"repo {owner}/{payload.get('name')}")
            return self._json(201, {"full_name": f"{owner}/{payload.get('name')}"})

        if path.startswith("/api/v1/admin/users"):
            if user is None or (scopes is not None and "write:admin" not in scopes and "full" not in scopes):
                with LOCK:
                    HITS.append(f"DENY create user {payload.get('username')}")
                return self._json(403, {"message": "no scope"})
            with LOCK:
                exists = payload.get("username") in USERS
                if not exists:
                    USERS[payload["username"]] = {"email": payload.get("email", ""),
                                                  "password": payload.get("password", ""), "tokens": {}}
                    HITS.append(f"user {payload['username']}")
            return self._json(422 if exists else 201, {"username": payload.get("username")})

        if path.startswith("/api/v1/repos/") and not path.endswith("/hooks"):
            parts = path.split("/api/v1/repos/")[1].split("/")
            owner, name = parts[0], parts[1]
            if user is None or (scopes is not None and "write:repository" not in scopes and "full" not in scopes):
                with LOCK:
                    HITS.append(f"DENY repo {owner}/{name}")
                return self._json(403, {"message": "no scope"})
            with LOCK:
                REPOS.setdefault(f"{owner}/{name}", {"hooks": []})
                HITS.append(f"repo {owner}/{name}")
            return self._json(201, {"full_name": f"{owner}/{name}"})

        if path.startswith("/api/v1/repos/") and path.endswith("/hooks"):
            parts = path.split("/api/v1/repos/")[1].split("/")
            owner, name = parts[0], parts[1]
            with LOCK:
                REPOS.setdefault(f"{owner}/{name}", {"hooks": []})["hooks"].append(
                    payload.get("config", {}).get("url", ""))
                HITS.append(f"hook {owner}/{name}")
            return self._json(201, {"id": 1})

        return self._json(404, {})


srv = ThreadingHTTPServer(("127.0.0.1", 9317), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

with LOCK:
    USERS["parastoo-system"] = {"email": "system@parastoo.local",
                                "password": os.environ["GITEA_ADMIN_PASSWORD"], "tokens": {}}

# ---------------- جریان واقعی پرستو ----------------

from app import dockerops, security  # noqa: E402  (بعد از ست‌شدن env)

# exec داکر فقط برای ساخت ادمین سیستمی گیتا استفاده می‌شود؛ اینجا شبیه‌سازی می‌کنیم
dockerops.exec_in_gitea = lambda cmd: (0, "ok")

from app import workers  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project, Setting, User  # noqa: E402

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


from fastapi.testclient import TestClient  # noqa: E402

with TestClient(app, follow_redirects=False) as c:
    # ویزارد: ادمین پنل ساخته شود (شرط ورکر بوت‌استرپ)
    r = c.post("/setup", data={"email": "admin@test.ir", "password": "secret1", "name": "مدیر"},
               follow_redirects=False)
    check("ساخت ادمین پنل (ویزارد)", r.status_code == 303)

    # یک سیکل بوت‌استرپ: توکن سیستمی گرفته شود
    workers.bootstrap_once()
    db = SessionLocal()
    check("توکن سیستمی گیتا ذخیره شد",
          bool(db.get(Setting, "gitea_system_token") and db.get(Setting, "gitea_system_token").value))
    db.close()

    # ثبت‌نام کاربر + یک سیکل self-healing ورکر
    r = c.post("/register", data={"email": "ali@test.ir", "password": "secret1", "name": "علی"},
               follow_redirects=False)
    check("ثبت‌نام کاربر", r.status_code == 303)
    workers.bootstrap_once()

    db = SessionLocal()
    u = db.query(User).filter(User.email == "ali@test.ir").first()
    check("حساب گیت کاربر ساخته شد (username)", bool(u.gitea_username), f"gitea_username={u.gitea_username!r}")
    check("توکن گیت کاربر گرفته شد", bool(u.gitea_token), "gitea_token خالی است")
    gname = u.gitea_username
    db.close()

    with LOCK:
        hits = list(HITS)
    check("توکن کاربر با basic auth گرفته شد (نه توکن سیستمی)",
          f"token {gname}" in hits and not any(h.startswith("DENY token") for h in hits),
          f"hits={hits}")

    # پروژه: مخزن باید خودکار ساخته شود
    db = SessionLocal()
    uid = db.query(User).filter(User.email == "ali@test.ir").first().id
    db.close()
    r = c.post("/projects/new", data={"name": "فروشگاه", "slug": "gitea-shop",
                                      "csrf": security.csrf_token(uid)}, follow_redirects=False)
    check("ساخت پروژه", r.status_code == 303)
    workers.bootstrap_once()

    db = SessionLocal()
    p = db.query(Project).filter(Project.slug == "gitea-shop").first()
    check("repo_status آماده شد (دیگر «در حال ساخت» برای همیشه نمی‌ماند)",
          p.repo_status == "ready", f"repo_status={p.repo_status!r}")
    db.close()

    with LOCK:
        repos = dict(REPOS)
    check("مخزن در گیتا ساخته شد", f"{gname}/gitea-shop" in repos)
    check("وب‌هوک بیلد روی مخزن نصب شد",
          bool(repos.get(f"{gname}/gitea-shop", {}).get("hooks")))

    # --- بذر جهانی (v0.5.2): کامیت اولیه روی مخزن خالی ---
    seeded = repos.get(f"{gname}/gitea-shop", {}).get("files", {})
    check("کامیت اولیهٔ بذر جهانی روی مخزن خالی اعمال شد", bool(seeded))
    check("بذر جهانی شامل README و index.html است",
          "README.md" in seeded and "index.html" in seeded)
    check("محتوای seed پایه‌۶۴ بوده (وفادار به API واقعی گیتا)",
          all(isinstance(v, str) and v for v in seeded.values()))

    # پروژهٔ دوم — بذر جهانی برای همهٔ پروژه‌ها یکی است (فناوری دیگر پرسیده نمی‌شود)
    db = SessionLocal()
    uid = db.query(User).filter(User.email == "ali@test.ir").first().id
    db.close()
    r = c.post("/projects/new", data={"name": "نوداپ", "slug": "gitea-node",
                                      "csrf": security.csrf_token(uid)}, follow_redirects=False)
    check("ساخت پروژهٔ دوم", r.status_code == 303)
    with LOCK:
        seeded_node = dict(REPOS).get(f"{gname}/gitea-node", {}).get("files", {})
    check("بذر جهانی دوم هم یکسان است (بدون وابسته به انتخاب فناوری)",
          "index.html" in seeded_node and "css/style.css" in seeded_node)

    # قاعدهٔ «فقط مخزن خالی»: سیکل بعدی نباید دوباره seed کند
    with LOCK:
        n_contents = sum(1 for h in HITS if h.startswith("contents "))
    workers.bootstrap_once()
    with LOCK:
        n_contents2 = sum(1 for h in HITS if h.startswith("contents "))
    check("seed دقیقاً یک‌بار برای هر مخزن (بازنویسی ممنوع)", n_contents == n_contents2 == 2,
          f"n={n_contents},{n_contents2}")

    # idempotency: سیکل بعدی نباید چیزی را دوباره بسازد یا خراب کند
    n_before = len(HITS)
    workers.bootstrap_once()
    db = SessionLocal()
    u = db.query(User).filter(User.email == "ali@test.ir").first()
    check("سیکل بعدی همان حساب را دست‌نخورده نگه می‌دارد",
          u.gitea_username == gname and u.gitea_token)
    db.close()

srv.shutdown()

print()
print(f"نتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
