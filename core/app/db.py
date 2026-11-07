"""راه‌اندازی دیتابیس (SQLAlchemy همگام — ساده و قابل‌اعتماد برای تک‌سرور)."""
import sqlalchemy
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


# ---------------------------------------------------------------- ریست اسکیما
# معماری سرویس‌محور (v0.4.0) اسکیمای پروژه‌محور را عوض می‌کند؛ پروژه در فاز تست
# است و به‌جای مهاجرت دادهٔ قدیمی، دادهٔ پروژه‌محور پاک و اسکیمای تازه ساخته می‌شود.
# حفظ می‌شود: users (ورود مالک/کاربران) و settings (دامنه/ویزارد/گیت).
# صفر می‌شود: کیف پول کاربران (دفتر WalletTx پاک می‌شود).

_LEGACY_TABLES = ("services", "payments", "wallet_tx", "builds", "sites", "projects")


def schema_is_current() -> bool:
    """آیا اسکیمای موجود، اسکیمای سرویس‌محور است؟"""
    insp = sqlalchemy.inspect(engine)
    tables = set(insp.get_table_names())
    if "services" not in tables or "projects" not in tables:
        return False
    cols = {c["name"] for c in insp.get_columns("projects")}
    return "manifest_json" in cols and "source_project_id" in cols


def reset_project_data() -> None:
    """پاک‌سازی کامل دادهٔ پروژه‌محور + ساخت اسکیمای تازه (فاز تست v0.4.0).

    idempotent است: اگر اسکیما تازه باشد هیچ کاری نمی‌کند.
    """
    if schema_is_current():
        return
    print("[parastoo] v0.4.0 service-oriented schema — resetting project data "
          "(test phase; users & settings are kept)")
    with engine.begin() as conn:
        for t in _LEGACY_TABLES:
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{t}"')
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        db.query(User).update({User.balance: 0})
        db.commit()
        print("[parastoo] schema reset done — user balances zeroed")
    finally:
        db.close()


from .models import User  # noqa: E402  (پس از ساخت engine — برای reset_project_data)
