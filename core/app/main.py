"""پرستو — کنترل‌پلین PaaS خودمیزبان. کارخانهٔ اپ FastAPI.

از v0.3.0 این فایل فقط «مونتاژ» است: میان‌افزار + استاتیک + روترها + هندلرهای
خطا. مسیرهای واقعی در routes/ هستند (خانه/داشبورد در routes/dashboard.py،
healthz و نسخه در routes/health.py). نام app.main:app حفظ شده چون Dockerfile
با uvicorn همین را صدا می‌زند و تست‌های smoke هم از app.main.app می‌خوانند.
"""
import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from . import devlog, workers
from .config import settings
from .db import Base, SessionLocal, engine
from .deps import render, resolve_user
from .routes import ROUTERS

log = logging.getLogger("parastoo")

WEB_STATIC = pathlib.Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    workers.start_workers()
    yield


# ---------- صفحات خطا (خارج از کارخانه تا تست‌پذیر باشند) ----------

# متن‌های پیش‌فرض Starlette انگلیسی‌اند؛ برای کاربر فارسی‌زبان به پیام روشن تبدیل می‌شوند
_ERROR_MESSAGES = {
    400: "درخواست ارسالی معتبر نیست.",
    403: "دسترسی به این بخش مجاز نیست.",
    404: "صفحه‌ای که دنبالش بودید پیدا نشد. اگر از داخل پنل به اینجا رسیده‌اید، مشکل را برای ما گزارش کنید.",
    405: "این عملیات روی این آدرس مجاز نیست.",
    429: "درخواست‌های شما زیاد است؛ کمی صبر کنید و دوباره تلاش کنید.",
}


def _error_page(request: Request, code: int, message: str):
    db = SessionLocal()
    try:
        user = resolve_user(request, db)
    finally:
        db.close()
    return render("error.html", {
        "request": request, "user": user, "code": code,
        "message": _ERROR_MESSAGES.get(code, message),
        "m": None, "flash": "", "path": request.url.path,
    }, status_code=code)


async def http_exception_handler(request: Request, exc: HTTPException):
    # ریدایرکت‌های احراز هویت (303) دست‌نخورده پاس می‌شوند
    if exc.status_code in (301, 302, 303, 307, 308) and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": str(exc.detail)}, status_code=exc.status_code)
    return _error_page(request, exc.status_code, str(exc.detail))


async def generic_exception_handler(request: Request, exc: Exception):
    # تا v0.2.11 خطاهای ۵۰۰ بی‌صدا بودند (هیچ‌جا ثبت نمی‌شدند)؛ حالا هم در
    # لاگ کانتینر و هم در رینگ‌بافر /api/dev/logs ثبت می‌شوند.
    log.exception("500 on %s %s: %r", request.method, request.url.path, exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)
    return _error_page(request, 500, "خطای داخلی سرور. لطفاً دوباره تلاش کنید.")


# ---------- کارخانه ----------

def create_app() -> FastAPI:
    devlog.attach()  # رینگ‌بافر لاگ — پیش از هر رندر گزارش لازم است

    application = FastAPI(
        title="Parastoo",
        version=settings.VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        """هدرهای امنیتی پایه برای همهٔ پاس‌های پنل (سایت‌ها از مسیر ترافیک می‌روند و مستقل‌اند)."""
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    application.mount("/static", StaticFiles(directory=str(WEB_STATIC)), name="static")

    for r in ROUTERS:
        application.include_router(r)

    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(Exception, generic_exception_handler)
    return application


app = create_app()
