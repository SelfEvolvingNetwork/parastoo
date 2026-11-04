"""پرستو — کنترل‌پلین PaaS خودمیزبان. نقطه ورود FastAPI."""
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import joinedload
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from . import workers
from .config import settings
from .db import Base, SessionLocal, engine
from .deps import ctx, get_current_user, render
from .models import Build, Project, Site, User
from .routes import (account, admin, dev, hooks, marketplace, projects, setup,
                     sites, wallet)

log = logging.getLogger("parastoo")

WEB_STATIC = pathlib.Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    workers.start_workers()
    yield


app = FastAPI(
    title="Parastoo",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """هدرهای امنیتی پایه برای همهٔ پاس‌های پنل (سایت‌ها از مسیر ترافیک می‌روند و مستقل‌اند)."""
    resp = await call_next(request)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp

app.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")

for r in (setup.router, account.router, projects.router, sites.router,
          marketplace.router, wallet.router, admin.router, hooks.router,
          dev.router):
    app.include_router(r)


def _db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/healthz")
def healthz():
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        db_ok = False
    return JSONResponse({
        "ok": db_ok, "app": "parastoo", "version": settings.VERSION,
        "commit": settings.DEPLOYED_COMMIT or None,
        "deployed_at": settings.DEPLOYED_AT or None,
    })


@app.get("/api/version")
def api_version():
    """نسخهٔ استقرارشده — عمومی و سبک (بدون دیتابیس، بدون احراز هویت).

    برای بررسی سریع «کدوم نسخه الان روی سرور است» بعد از آپدیت خودکار:
      curl https://panel.example.com/api/version
    """
    return JSONResponse({
        "ok": True, "app": "parastoo", "version": settings.VERSION,
        "commit": settings.DEPLOYED_COMMIT or None,
        "deployed_at": settings.DEPLOYED_AT or None,
        "dev_mode": settings.DEV_MODE,
    })


@app.get("/")
def home(request: Request, user=Depends(get_current_user)):
    db = SessionLocal()
    try:
        has_admin = db.query(User).filter(User.is_admin.is_(True)).first() is not None
    finally:
        db.close()
    if not has_admin:
        return RedirectResponse("/setup", status_code=303)
    if user is None:
        return render("landing.html", ctx(request, None))
    # پیام‌های گارد (m=csrf / m=denied و …) که به / آمده‌اند در مسیر /
    # تا /dashboard حفظ می‌شوند — وگرنه دلیلِ مسدودشدن کاربر گم می‌شد.
    m = request.query_params.get("m")
    return RedirectResponse(f"/dashboard?m={m}" if m else "/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request, user=Depends(get_current_user)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    db = SessionLocal()
    try:
        my_projects = (db.query(Project)
                       .filter(Project.owner_id == user.id)
                       .order_by(Project.id.desc()).all())
        my_sites = (db.query(Site)
                    .filter(Site.owner_id == user.id)
                    .order_by(Site.id.desc()).all())
        # joinedload: قالب بعد از بسته‌شدن نشست به b.project دست می‌زند؛ بدون eager
        # load، lazy load روی آبجکت جداشده → DetachedInstanceError → 500 داشبورد
        # برای هر کاربری که حداقل یک بیلد دارد (باگ v0.2.21 و قبل‌تر).
        recent_builds = (db.query(Build)
                         .options(joinedload(Build.project))
                         .join(Project)
                         .filter(Project.owner_id == user.id)
                         .order_by(Build.id.desc()).limit(6).all())
    finally:
        db.close()
    return render("dashboard.html", ctx(request, user,
                                        projects=my_projects,
                                        sites=my_sites,
                                        builds=recent_builds))


def _error_page(request: Request, code: int, message: str):
    token = request.cookies.get("parastoo_session")
    user = None
    if token:
        from .security import read_session
        uid = read_session(token)
        if uid is not None:
            db = SessionLocal()
            try:
                user = db.get(User, uid)
            finally:
                db.close()
    return render("error.html", {
        "request": request, "user": user, "code": code, "message": message,
        "m": None, "flash": "", "path": request.url.path,
    }, status_code=code)


@app.exception_handler(HTTPException)
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # ریدایرکت‌های احراز هویت (303) دست‌نخورده پاس می‌شوند
    if exc.status_code in (301, 302, 303, 307, 308) and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": str(exc.detail)}, status_code=exc.status_code)
    return _error_page(request, exc.status_code, str(exc.detail))


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # تا v0.2.11 خطاهای ۵۰۰ بی‌صدا بودند (هیچ‌جا ثبت نمی‌شدند)؛ حالا هم در
    # لاگ کانتینر و هم در رینگ‌بافر /api/dev/logs ثبت می‌شوند.
    log.exception("500 on %s %s: %r", request.method, request.url.path, exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)
    return _error_page(request, 500, "خطای داخلی سرور. لطفاً دوباره تلاش کنید.")
