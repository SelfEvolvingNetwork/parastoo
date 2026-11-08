"""صفحهٔ عمومی دانلودها (/download) — v0.5.9: اپ ادمین «پرستو موبایل».

منبع فایل: بیلد CI مخزن parastoo-mobile که ورکر mobile-release آن را از
رلیز گیت‌هاب می‌کشد (services/mobile.py). اینجا فقط «سرو» است: صفحهٔ
راهنما + فایل APK با MIME درست. هیچ رمزی در این صفحه نیست — امنیت اپ به
پین گواهی + توکن ادمین روی خود اپ است، نه به مخفی‌ماندن فایلش.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import FileResponse

from ..db import get_db
from ..deps import ctx, get_current_user, render
from ..services import mobile

router = APIRouter()

APK_MIME = "application/vnd.android.package-archive"


@router.get("/download")
def download_page(request: Request,
                  user=Depends(get_current_user),
                  db=Depends(get_db)):  # db فقط برای هم‌الگویی وابستگی‌ها
    return render("download.html", ctx(request, user, **mobile.status()))


@router.get("/download/parastoo-mobile.apk")
def download_apk():
    path = mobile.apk_path()
    if path is None:
        raise HTTPException(status_code=404,
                            detail="هنوز بیلدی از پرستو موبایل منتشر نشده است")
    return FileResponse(
        path,
        media_type=APK_MIME,
        filename=mobile.APK_FILENAME,
        headers={"X-Content-Type-Options": "nosniff"},
    )
