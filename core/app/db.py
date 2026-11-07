"""راه‌اندازی دیتابیس (SQLAlchemy همگام — ساده و قابل‌اعتماد برای تک‌سرور).

قاعدهٔ ثابت از v0.5.0: اسکیما فقط از مدل‌های همین کد ساخته می‌شود
(`Base.metadata.create_all` در lifespan) — هیچ مهاجرت، تشخیص اسکیمای قدیمی
یا شیم سازگاری وجود ندارد. دیتای واقعی که نیست؛ اگر مدل عوض شد، دیتابیس
ریست می‌شود (فرمان مالک — فاز تست 2026-11-07).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def _make_engine():
    url = settings.DATABASE_URL
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: یک نشست دیتابیس برای هر درخواست."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_now():
    """زمان UTC همیشه برای محاسبه ساعتی."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)

