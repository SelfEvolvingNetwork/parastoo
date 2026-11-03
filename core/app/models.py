"""مدل‌های دیتابیس پرستو."""
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # کیف پول (تومان) — دفتر کل در WalletTx نگه داشته می‌شود
    balance: Mapped[int] = mapped_column(Integer, default=0)

    # حساب گیت خودکارِ کاربر (ساخته‌شده توسط پرستو در Gitea)
    gitea_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # نکته امنیتی MVP: رمز حساب گیت برای ساخت توکن نگه داشته می‌شود؛ در نسخه بعد رمزنگاری می‌شود
    gitea_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gitea_token: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime)

    def __init__(self, **kw):
        kw.setdefault("created_at", _now())
        super().__init__(**kw)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    stack: Mapped[str] = mapped_column(String(20))          # static | node | python | docker
    start_cmd: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # مارکت‌پلیس
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)   # انتشار در مارکت
    price: Mapped[int] = mapped_column(Integer, default=0)            # 0 = رایگان
    installs: Mapped[int] = mapped_column(Integer, default=0)

    # مخزن گیت
    repo_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|error
    webhook_secret: Mapped[str] = mapped_column(String(64))

    # آخرین وضعیت بیلد
    latest_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latest_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    build_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime)

    owner: Mapped["User"] = relationship()
    builds: Mapped[list["Build"]] = relationship(back_populates="project")
    sites: Mapped[list["Site"]] = relationship(back_populates="project")

    def __init__(self, **kw):
        kw.setdefault("created_at", _now())
        super().__init__(**kw)


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|success|failed
    commit_sha: Mapped[str] = mapped_column(String(40), default="")
    image_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="builds")

    def __init__(self, **kw):
        kw.setdefault("created_at", _now())
        super().__init__(**kw)


class Site(Base):
    """سایت منتشرشده = یک کانتینر در حال اجرا روی سرور پرستو."""
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("slug", name="uq_site_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # مالک سایت (ممکن است خریدار باشد)
    slug: Mapped[str] = mapped_column(String(60))
    subdomain: Mapped[str] = mapped_column(String(255))
    container_name: Mapped[str] = mapped_column(String(120))
    internal_port: Mapped[int] = mapped_column(Integer, default=3000)
    tier: Mapped[str] = mapped_column(String(20), default="nano")  # nano|small|medium
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|stopped|paused|error
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_charged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    project: Mapped["Project"] = relationship(back_populates="sites")
    owner: Mapped["User"] = relationship()

    def __init__(self, **kw):
        kw.setdefault("created_at", _now())
        super().__init__(**kw)


class WalletTx(Base):
    """دفتر کل تراکنش‌های مالی."""
    __tablename__ = "wallet_tx"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # مثبت = واریز، منفی = برداشت
    kind: Mapped[str] = mapped_column(String(30))  # deposit|charge|purchase|sale|admin_credit
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped["User"] = relationship()

    def __init__(self, **kw):
        kw.setdefault("created_at", _now())
        super().__init__(**kw)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


def _now() -> datetime:
    from .db import db_now
    return db_now()
