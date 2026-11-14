"""راه‌اندازی دیتابیس (SQLAlchemy همگام — ساده و قابل‌اعتماد برای تک‌سرور).

قاعدهٔ خودتوسعه‌دهنده (فرمان مالک ۲۰۲۶-۱۱-۱۴ — تکامل قانون v0.5.0): اسکیما
از مدل‌های همین کد ساخته می‌شود (`create_all` در lifespan) و تکامل اسکیما
مهاجرت خودکارِ افزایشی دارد (`auto_migrate` در همان lifespan) — ستونِ غایب
با `ALTER TABLE ADD COLUMN` افزوده می‌شود و داده ماندگار است؛ ریستِ
دیتابیس پیش‌فرض نیست. حذف/تغییر نام/تغییر نوع عمداً مهاجرت خودکار نیست —
چرخهٔ صریح با استدلال مکتوب می‌خواهد (سادگی > پیچیدگی صوری).
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


def auto_migrate(engine) -> list[str]:
    """مهاجرت خودکار افزایشی (حالت خودتوسعه‌دهنده — فرمان مالک ۲۰۲۶-۱۱-۱۴).

    ستون‌های غایبِ مدل‌ها را روی دیتابیس زنده با «ALTER TABLE ... ADD COLUMN»
    می‌افزاید — بدون حذف، بدون تغییر نام/نوع، بدون ریست؛ داده ماندگار است.

    - جداول غایب را create_all ساخته است؛ اینجا فقط جداول موجود.
    - ستون تازه همیشه بدون NOT NULL/FK/server_default افزوده می‌شود تا ALTER
      روی جدولِ دارای ردیف همیشه موفق باشد (قانون افزودنیِ نرم)؛ سخت‌گیریِ
      قیدها برای ردیف‌های تازه در لایهٔ مدل برقرار می‌ماند.
    - idempotent است؛ خروجی: فهرست «table.column»های افزوده (برای لاگ).
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in existing]
        if not missing:
            continue
        with engine.begin() as conn:
            prep = conn.dialect.identifier_preparer
            tname = prep.quote(table.name)
            for col in missing:
                ddl = (
                    f"ALTER TABLE {tname} ADD COLUMN {prep.quote(col.name)} "
                    f"{col.type.compile(engine.dialect)}"
                )
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added

