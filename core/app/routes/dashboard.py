"""خانه (/) و داشبورد — تا v0.2.24 در main.py بودند؛ حالا مسیرهای وب یکجا."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import joinedload

from ..db import get_db
from ..deps import ctx, get_current_user, render
from ..models import Build, Project, Site, User

router = APIRouter()


@router.get("/")
def home(request: Request,
         user=Depends(get_current_user),
         db=Depends(get_db)):
    has_admin = db.query(User).filter(User.is_admin.is_(True)).first() is not None
    if not has_admin:
        return RedirectResponse("/setup", status_code=303)
    if user is None:
        return render("landing.html", ctx(request, None))
    # پیام‌های گارد (m=csrf / m=denied و …) که به / آمده‌اند در مسیر /
    # تا /dashboard حفظ می‌شوند — وگرنه دلیلِ مسدودشدن کاربر گم می‌شد.
    m = request.query_params.get("m")
    return RedirectResponse(f"/dashboard?m={m}" if m else "/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request,
              user=Depends(get_current_user),
              db=Depends(get_db)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
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
    return render("dashboard.html", ctx(request, user,
                                        projects=my_projects,
                                        sites=my_sites,
                                        builds=recent_builds))
