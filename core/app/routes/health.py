"""نقطه‌های سلامت و نسخه — عمومی، سبک، بدون احراز هویت.

برای بررسی سریع «کدوم نسخه الان روی سرور است» بعد از آپدیت خودکار:
  curl https://panel.example.com/api/version
"""
from fastapi import APIRouter
from sqlalchemy import text
from starlette.responses import JSONResponse

from ..config import settings
from ..db import SessionLocal

router = APIRouter()


@router.get("/healthz")
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


@router.get("/api/version")
def api_version():
    return JSONResponse({
        "ok": True, "app": "parastoo", "version": settings.VERSION,
        "commit": settings.DEPLOYED_COMMIT or None,
        "deployed_at": settings.DEPLOYED_AT or None,
        "dev_mode": settings.DEV_MODE,
    })
