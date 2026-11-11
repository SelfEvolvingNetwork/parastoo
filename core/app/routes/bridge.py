"""مسیرهای پل عامل پروژه (v0.4.3) — /api/bridge/* + شیم گیت /bridge/git/*.

دو گروه مسیر:
1) API پل (Bearer توکن عامل): وضعیت، بیلدها، لاگ بیلد/کانتینر، exec،
   restart، env، مانیفست، audit، صف بیلد. خطاها JSON است (هندلر /api/).
2) شیم گیت: مسیرهای پروتکل گیت هوشمند (smart HTTP) که توکن عامل را
   می‌پذیرد و بایت‌به‌بایت به مخزنِ «همان پروژه» در گیتا پروکسی می‌کند —
   عامل با `git push` معمولی کار می‌کند و به توکن گیتِ حسابِ کاربر
   (که دامنه‌اش همهٔ مخزن‌هاست) هیچ دسترسی‌ای ندارد. مخزن مقصد همیشه از
   دیتابیس ساخته می‌شود (owner/slug پروژهٔ توکن)، نه از URL کلاینت.
"""
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import dockerops, workers
from ..db import get_db
from ..models import Build, Project
from ..services import bridge as bsvc

router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
_GIT_PATH_RE = re.compile(r"^(?P<slug>[^/]+?)(?:\.git)?/(?P<action>info/refs|git-upload-pack|git-receive-pack)$")
_GIT_SERVICES = {"git-upload-pack", "git-receive-pack"}

# هدرهایی که بین کلاینت گیت و گیتا رد و بدل می‌شود (بقیه حذف)
_GIT_FWD_HEADERS = ("content-type", "git-protocol", "accept", "user-agent")


# ---------- احراز و ابزار مشترک ----------

def _auth(request: Request, db: Session, bucket: str | None = None):
    """توکن عامل → (ردیف، پروژه)؛ هر شکست = 401. سقف نرخ اختیاری همین‌جا."""
    row = bsvc.authenticate(db, bsvc.token_from_headers(request))
    if row is None:
        raise HTTPException(401, "توکن پل نامعتبر یا باطل‌شده است")
    token, project = row
    if bucket and not bsvc.allow(token.id, bucket):
        bsvc.audit(token, project, bucket, "rate limited", request.client.host if request.client else "", ok=False)
        raise HTTPException(429, "سقف نرخ فراخوانی پل پر شد — کمی صبر کنید")
    return token, project


def _svc_or_404(project: Project, svc: str | None) -> str:
    """نام سرویس معتبرِ همین پروژه — پیش‌فرض: سرویس عمومی یا اولین سرویس."""
    names = sorted(s.name for s in project.services)
    if not names:
        raise HTTPException(404, "پروژه هنوز سرویسی ندارد (اول بیلد بگیرد)")
    if svc is None or svc == "":
        pub = next((s.name for s in project.services if s.is_public), None)
        return pub or names[0]
    if svc not in names:
        raise HTTPException(404, f"سرویس «{svc}» در این پروژه وجود ندارد — سرویس‌ها: {', '.join(names)}")
    return svc


def _docker_unavailable(e: dockerops.DockerUnavailable):
    raise HTTPException(503, f"داکر در دسترس نیست: {e}")


# ---------- API پل ----------

@router.get("/api/bridge/status")
def status(request: Request, db: Session = Depends(get_db)):
    token, project = _auth(request, db, "status")
    data = bsvc.project_status(db, project)
    bsvc.audit(token, project, "status", ip=request.client.host if request.client else "")
    return data


@router.get("/api/bridge/manifest")
def manifest(request: Request, db: Session = Depends(get_db)):
    token, project = _auth(request, db, "status")
    import json as _json
    data = _json.loads(project.manifest_json) if project.manifest_json else None
    bsvc.audit(token, project, "manifest", ip=request.client.host if request.client else "")
    return {"ok": True, "manifest": data, "latest_sha": project.latest_sha}


@router.get("/api/bridge/builds")
def builds(request: Request, limit: int = 10, db: Session = Depends(get_db)):
    token, project = _auth(request, db, "status")
    rows = (db.query(Build)
            .filter(Build.project_id == project.id)
            .order_by(Build.id.desc())
            .limit(max(1, min(limit, 50)))
            .all())
    bsvc.audit(token, project, "builds", ip=request.client.host if request.client else "")
    return {"ok": True, "builds": [{
        "id": b.id, "status": b.status, "sha": b.commit_sha[:7],
        "created_at": b.created_at.isoformat(sep=" "),
        "finished_at": b.finished_at.isoformat(sep=" ") if b.finished_at else None,
    } for b in rows]}


@router.get("/api/bridge/builds/{bid}")
def build_detail(request: Request, bid: int, tail: int = 8000,
                 db: Session = Depends(get_db)):
    token, project = _auth(request, db, "status")
    b = (db.query(Build)
         .filter(Build.id == bid, Build.project_id == project.id)  # دانهٔ پروژه
         .first())
    if b is None:
        raise HTTPException(404, "بیلد یافت نشد")
    bsvc.audit(token, project, "build_log", f"#{bid}", request.client.host if request.client else "")
    log = b.log or ""
    tail = max(200, min(tail, 60000))
    return {"ok": True, "id": b.id, "status": b.status, "sha": b.commit_sha[:7],
            "created_at": b.created_at.isoformat(sep=" "),
            "finished_at": b.finished_at.isoformat(sep=" ") if b.finished_at else None,
            "log": log[-tail:], "log_truncated": len(log) > tail}


@router.post("/api/bridge/build")
async def build_enqueue(request: Request, db: Session = Depends(get_db)):
    """صف کردن بیلد (استقرار آخرین کامیت push‌شده) — معادل دکمهٔ «بیلد مجدد»."""
    token, project = _auth(request, db, "build")
    if not project.source_project_id and project.repo_status != "ready":
        raise HTTPException(409, "مخزن گیت پروژه هنوز آماده نیست")
    bid = workers.enqueue_build(project.id, project.latest_sha or "")
    if bid is None:
        raise HTTPException(500, "صف بیلد در دسترس نیست")
    bsvc.audit(token, project, "build", f"queued #{bid}", request.client.host if request.client else "")
    return {"ok": True, "build_id": bid}


@router.get("/api/bridge/logs")
def logs(request: Request, svc: str | None = None, tail: int = 200,
         db: Session = Depends(get_db)):
    token, project = _auth(request, db, "logs")
    name = _svc_or_404(project, svc)
    try:
        text = dockerops.service_logs(project.id, name, tail=tail)
    except dockerops.DockerUnavailable as e:
        _docker_unavailable(e)
    bsvc.audit(token, project, "logs", f"svc={name} tail={tail}",
               request.client.host if request.client else "")
    return {"ok": True, "svc": name, "log": text[-bsvc.LOGS_BYTES_MAX:]}


@router.post("/api/bridge/exec")
async def exec_cmd(request: Request, db: Session = Depends(get_db)):
    """اجرای دستور داخل کانتینر سرویس — ابزار تست/عیب‌یابی عامل.

    سقف‌ها: تایم‌اوت ≤۱۲۰ث، خروجی ≤۲۵۶KB، سرویس فقط از همین پروژه.
    """
    token, project = _auth(request, db, "exec")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "بدنهٔ JSON نامعتبر است")
    cmd = str(body.get("cmd") or "").strip()
    if not cmd:
        raise HTTPException(400, "دستور (cmd) خالی است")
    if len(cmd) > 4000:
        raise HTTPException(400, "دستور بیش از حد بلند است (سقف ۴۰۰۰ کاراکتر)")
    timeout_s = body.get("timeout_s") or 60
    try:
        timeout_s = max(1, min(int(timeout_s), bsvc.EXEC_TIMEOUT_MAX))
    except (TypeError, ValueError):
        timeout_s = 60
    name = _svc_or_404(project, body.get("svc"))
    try:
        res = dockerops.exec_in_service(project.id, name, cmd, timeout_s=timeout_s,
                                        max_bytes=bsvc.EXEC_OUTPUT_MAX)
    except dockerops.DockerUnavailable as e:
        _docker_unavailable(e)
    bsvc.audit(token, project, "exec", f"svc={name} exit={res.get('exit_code')} cmd={cmd[:120]}",
               request.client.host if request.client else "", ok=res.get("exit_code") == 0)
    return {"ok": True, "svc": name, **res}


@router.post("/api/bridge/restart")
async def restart(request: Request, db: Session = Depends(get_db)):
    token, project = _auth(request, db, "exec")
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = _svc_or_404(project, body.get("svc"))
    try:
        dockerops.restart_service(project.id, name)
    except dockerops.DockerUnavailable as e:
        _docker_unavailable(e)
    bsvc.audit(token, project, "restart", f"svc={name}",
               request.client.host if request.client else "")
    return {"ok": True, "svc": name, "restarted": True}


@router.get("/api/bridge/env")
def env(request: Request, db: Session = Depends(get_db)):
    """متغیرهای محیطی سرویس‌های همین پروژه — رازهای پروژهٔ خودِ کاربر است."""
    token, project = _auth(request, db, "env")
    bsvc.audit(token, project, "env", ip=request.client.host if request.client else "")
    return {"ok": True, "services": {s.name: s.env() for s in project.services}}


@router.get("/api/bridge/audit")
def audit(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    """گزارش اقدامات پل — عامل فقط رکوردهای توکن خودش را می‌بیند."""
    token, project = _auth(request, db, "audit")
    return {"ok": True, "records": bsvc.audit_tail(limit, token_prefix=token.prefix)}


# ---------- شیم گیت: /bridge/git/<slug>.git/* ----------

def _challenge(msg: str, status: int = 401):
    """پاسخ گیت‌پسند — هدر WWW-Authenticate لازم است تا git پسورد بپرسد/بفرستد."""
    return Response(msg, status_code=status,
                    headers={"WWW-Authenticate": 'Basic realm="parastoo-bridge"',
                             "Cache-Control": "no-cache"})


def _repo_guard(db: Session, project: Project) -> tuple[str, str] | None:
    """گاردهای مخزن → مسیر (owner, slug) در گیتا؛ ناموفق = None (پیام قبلاً داده شده).

    ترتیب عمدی: نصب مارکت (بدون مخزن) → 404 قطعی؛ مخزن pending → 503 انتظار؛
    حساب گیتِ ناقص مالک → 503 خودترمیمی در جریان. اعتبار پروکسی هرگز به
    کلاینت نمی‌رود و مخزن مقصد همیشه از دیتابیس می‌آید، نه از URL کلاینت.
    """
    if project.source_project_id:
        return None  # نصب مارکت‌پلیس — مخزن ندارد
    if project.repo_status != "ready":
        return None  # مخزن هنوز ساخته نشده (pending/error)
    owner = project.owner
    if not owner.gitea_username or not owner.gitea_token:
        return None
    return owner.gitea_username, project.slug


async def _git_shim(request: Request, repo_path: str, db: Session):
    m = _GIT_PATH_RE.match(repo_path or "")
    if m is None or not _SLUG_RE.match(m.group("slug")):
        return _challenge("پل گیت پرستو — مسیر درست: /bridge/git/<slug>.git\n", 404)
    slug = m.group("slug")
    action = m.group("action")

    raw = bsvc.token_from_headers(request)
    auth = bsvc.authenticate(db, raw)
    if auth is None:
        return _challenge("توکن پل نامعتبر است (Authorization Basic با رمزِ توکن عامل)\n")
    token, project = auth
    if project.slug != slug:
        bsvc.audit(token, project, "git", f"cross-project denied: {slug}", ok=False)
        return _challenge("این توکن به مخزن پروژهٔ دیگری تعلق دارد\n", 403)
    target = _repo_guard(db, project)
    if target is None:
        if project.source_project_id:
            return _challenge("این پروژه مخزن گیت ندارد (نصب مارکت‌پلیس)\n", 404)
        return _challenge("مخزن گیت پروژه هنوز آماده نیست — چند لحظه بعد دوباره تلاش کنید\n", 503)
    if not bsvc.allow(token.id, "git"):
        bsvc.audit(token, project, "git", "rate limited", ok=False)
        return _challenge("سقف نرخ عملیات گیت پر شد — کمی صبر کنید\n", 429)

    owner_name, repo = target
    upstream = settings_gitea_internal().rstrip("/")
    gitea_path = f"/{owner_name}/{repo}.git/{action}"
    auth_up = (owner_name, project.owner.gitea_token)

    fwd = {k: v for k, v in request.headers.items()
           if k.lower() in _GIT_FWD_HEADERS and k.lower() != "accept"}

    try:
        client = httpx.AsyncClient(
            base_url=upstream, auth=auth_up,
            timeout=httpx.Timeout(connect=10, read=600, write=600, pool=600),
        )
    except Exception as e:  # pragma: no cover
        return _challenge(f"proxy error: {e}\n", 502)

    try:
        if action == "info/refs":
            service = request.query_params.get("service", "")
            if service not in _GIT_SERVICES:
                return _challenge("سرویس گیت نامعتبر است\n", 400)
            up_req = client.build_request("GET", gitea_path, params={"service": service},
                                          headers=fwd)
        else:
            up_req = client.build_request(
                "POST", gitea_path,
                content=_capped_body(request, bsvc.MAX_PUSH_BYTES),
                headers=fwd)

        resp = await client.send(up_req, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return _challenge("گیتا در دسترس نیست — دوباره تلاش کنید\n", 502)
    except _TooBig:
        await client.aclose()
        return _challenge(f"حجم push بیش از سقف {bsvc.MAX_PUSH_BYTES // (1024 * 1024)}MB است\n", 413)
    except httpx.HTTPError as e:
        await client.aclose()
        return _challenge(f"خطای پروکسی گیت: {type(e).__name__}\n", 502)
    except Exception:
        await client.aclose()
        return _challenge("خطای ناشناختهٔ پل گیت\n", 502)

    # پاسخ استریم‌شده — هدر محتوایی که گیت لازم دارد حفظ می‌شود
    out_headers = {}
    for k in ("content-type",):
        if k in resp.headers:
            out_headers[k] = resp.headers[k]
    out_headers.setdefault("Cache-Control", "no-cache")
    if resp.status_code in (401, 403):
        await resp.aclose()
        await client.aclose()
        bsvc.audit(token, project, "git", f"gitea denied {action}", ok=False)
        return _challenge("اعتبار پروکسی به مخزن رد شد — وضعیت پروژه را چک کنید\n", 502)

    async def relay():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    bsvc.audit(token, project, "git", f"{action} {request.method}")
    return StreamingResponse(relay(), status_code=resp.status_code,
                             headers=out_headers)


class _TooBig(Exception):
    pass


def _capped_body(request: Request, cap: int):
    """استریم بدنهٔ request با سقف بایت — از میانهٔ راه هم قطع می‌شود."""
    async def gen():
        got = 0
        async for chunk in request.stream():
            got += len(chunk)
            if got > cap:
                raise _TooBig()
            yield chunk
    return gen()


def settings_gitea_internal() -> str:
    from ..config import settings
    return settings.GITEA_INTERNAL_URL


@router.api_route("/bridge/git/{repo_path:path}", methods=["GET", "POST"])
async def git_shim(request: Request, repo_path: str, db: Session = Depends(get_db)):
    return await _git_shim(request, repo_path, db)


# ---------- جفت‌سازی عامل پروژه (v0.5.27 — عمومی، بدون توکن) ----------
# آینهٔ پل ادمین (access-agent v0.7.1) با دانهٔ پروژه: عامل رمز ۵ رقمی
# خودش را ادعا می‌کند، صاحب پروژه در صفحهٔ پروژه تأییدش می‌کند و عامل
# توکن pst_ تازه برمی‌دارد. هیچ رازی از چت عبور نمی‌کند؛ متن دعوت
# بی‌رمز است و لورفتنش بی‌خطر است (سقف‌ها و هش‌ها در services/agentpair.py).

@router.post("/api/agent/claim")
async def agent_claim(request: Request, db: Session = Depends(get_db)):
    """ادعای جفت‌سازی — بدنه: {"project": "<slug>", "code": "<5 رقم ASCII>"}."""
    from ..services import agentpair as apair
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "بدنهٔ JSON نامعتبر است")
    slug = str(body.get("project") or "").strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(404, "پروژهٔ موردنظر یافت نشد")
    project = (db.query(Project).filter(Project.slug == slug).first())
    if project is None:
        raise HTTPException(404, "پروژهٔ موردنظر یافت نشد")
    ip = request.client.host if request.client else ""
    res = apair.mint_claim(project.id, str(body.get("code") or ""), ip)
    if not res.get("ok"):
        apair.audit_pair("claim_refused", f"slug={slug} error={res.get('error')}",
                         ip, ok=False)
        raise HTTPException(res.get("status", 400), res.get("detail") or res.get("error"))
    apair.audit_pair("claim", f"slug={slug} claim_id={res.get('claim_id')}", ip)
    return res


@router.post("/api/agent/claim/poll")
async def agent_claim_poll(request: Request, db: Session = Depends(get_db)):
    """برداشت توکن — بدنه: {"claim_id": "...", "claim_secret": "..."}.

    وضعیت‌ها: pending / approved (+pst_) / expired / unknown. مقدار رمز یا
    راز هیچ‌گاه در پاسخ، دیسک یا audit بازنمی‌گردد."""
    from ..services import agentpair as apair
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "بدنهٔ JSON نامعتبر است")
    ip = request.client.host if request.client else ""
    res = apair.collect(db, str(body.get("claim_id") or ""),
                        str(body.get("claim_secret") or ""))
    if res.get("status") == "approved":
        apair.audit_pair("token_issued",
                         f"slug={res.get('project')} prefix={res.get('token_prefix')}", ip)
    return res
