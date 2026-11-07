"""مدل‌های دیتابیس پرستو — معماری سرویس‌محور (v0.4.0).

قاعدهٔ طلایی معماری جدید: موتور فقط یک اسم می‌شناسد — «سرویس».
پروژه مالک ۱ تا N سرویس است (N=1 حالت پیش‌فرضِ بدون مانیفست)؛ هر سرویس
در اجرا یک کانتینر است که فقط روی شبکهٔ خصوصی همان پروژه زندگی می‌کند.
«سایت» دیگر کانتینر نیست؛ فقط نقطهٔ ورود عمومی (زیردامنه) است که به
سرویسِ عمومیِ پروژه روت می‌شود. منابع و صورتحساب در سطح سرویس است.

created_at در همهٔ مدل‌ها با default ستونی db_now پر می‌شود.
"""
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, db_now


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

    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)


class Project(Base):
    """پروژه = گروه سرویس‌ها + (اختیاری) مخزن گیت + (اختیاری) تعریف مارکت.

    stack فقط «قالب شروع» است (بذر مخزن خالی + برچسب نمایشی) — موتور آن را
    نمی‌خواند؛ تشخیص فناوری در زمان بیلد از محتوای مخزن انجام می‌شود و
    parastoo.yml همیشه بر همه چیز مقدم است.
    source_project_id: پرستوی نصب‌شده از مارکت‌پلیس — پروژهٔ مالکِ خریدار که
    سرویس‌هایش کپی همان سرویس‌های پروژهٔ فروشنده است (بدون مخزن گیت).
    manifest_json: عکس لحظه‌ای مانیفست آخرین بیلد موفق (برای UI و انتشار).
    """
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    stack: Mapped[str] = mapped_column(String(20), default="static")   # قالب شروع — نمایشی
    description: Mapped[str] = mapped_column(Text, default="")

    # مارکت‌پلیس
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)   # انتشار در مارکت
    price: Mapped[int] = mapped_column(Integer, default=0)            # 0 = رایگان
    installs: Mapped[int] = mapped_column(Integer, default=0)

    # نصب مارکت‌پلیس (پروژهٔ خریدار → پروژهٔ فروشنده)
    source_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True)

    # مخزن گیت (نصب‌های مارکت مخزن ندارند — repo_status=ready می‌ماند)
    repo_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|ready|error
    webhook_secret: Mapped[str] = mapped_column(String(64))

    # آخرین وضعیت بیلد
    latest_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)

    owner: Mapped["User"] = relationship()
    builds: Mapped[list["Build"]] = relationship(back_populates="project")
    sites: Mapped[list["Site"]] = relationship(back_populates="project")
    services: Mapped[list["Service"]] = relationship(back_populates="project")


class Service(Base):
    """سرویس = تعریف یک کانتینرِ پروژه؛ تنها واحدی که موتور می‌شناسد.

    منابع (tier) و کسر ساعتی در همین سطح است؛ جمع سرویس‌های runningِ
    پروژه = هزینهٔ پروژه. is_public فقط یک سرویس است و به ترافیک روت می‌شود.
    status: stopped (فقط تعریف/آماده) | running | paused (بدهی) | error
    env_json: دیکشنری {"KEY": "val"} — volumes_json: لیست جفت‌های [نام, مسیر].
    """
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_service_proj_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, default=3000)
    command: Mapped[str] = mapped_column(Text, default="")
    env_json: Mapped[str] = mapped_column(Text, default="{}")
    volumes_json: Mapped[str] = mapped_column(Text, default="[]")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    tier: Mapped[str] = mapped_column(String(20), default="nano")
    status: Mapped[str] = mapped_column(String(20), default="stopped")
    container_name: Mapped[str] = mapped_column(String(120), default="")
    last_charged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)

    project: Mapped["Project"] = relationship(back_populates="services")

    def env(self) -> dict:
        import json
        try:
            return json.loads(self.env_json or "{}")
        except Exception:
            return {}

    def volumes(self) -> list[tuple[str, str]]:
        import json
        try:
            return [(a, b) for a, b in json.loads(self.volumes_json or "[]")]
        except Exception:
            return []


class Site(Base):
    """سایت = نقطهٔ ورود عمومی یک پروژه (زیردامنه) — دیگر کانتینر نیست.

    هر تعداد سایت می‌تواند به سرویس عمومیِ همان پروژه روت شود؛ افزودن/حذف
    سایت فقط روتینگ را بازتولید می‌کند (بازآفرینی کانتینرهای پروژه با لیبل
    ترافیک تازه). منابع و صورتحساب در سرویس‌ها است، نه اینجا.
    status آینهٔ وضعیت کانتینرهای پروژه است.
    """
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("slug", name="uq_site_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # مالک سایت (ممکن است خریدار باشد)
    slug: Mapped[str] = mapped_column(String(60))
    subdomain: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|stopped|paused|error

    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)

    project: Mapped["Project"] = relationship(back_populates="sites")
    owner: Mapped["User"] = relationship()


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|success|failed
    commit_sha: Mapped[str] = mapped_column(String(40), default="")
    log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="builds")


class WalletTx(Base):
    """دفتر کل تراکنش‌های مالی."""
    __tablename__ = "wallet_tx"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)  # مثبت = واریز، منفی = برداشت
    kind: Mapped[str] = mapped_column(String(30))  # deposit|charge|purchase|sale|admin_credit
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)

    user: Mapped["User"] = relationship()


class Payment(Base):
    """درخواست شارژ آنلاین کیف پول از درگاه پرداخت (فعلاً درگاه آزمایشی).

    Payment فقط «درخواست» است: تا وقتی verify درگاه تأیید نکند هیچ پولی به
    کیف نمی‌نشیند. token رشتهٔ نشدنی است که در URLهای رفت‌وبرگشت به درگاه
    می‌آید؛ gateway_ref رسید درگاه هنگام تأیید است.
    """
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    gateway: Mapped[str] = mapped_column(String(30), default="mock")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|paid|canceled|failed
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    gateway_ref: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship()


class AgentToken(Base):
    """توکن پل عامل پروژه (v0.4.3) — دسترسی عامل هوش مصنوعیِ کاربر به «فقط همین پروژه».

    عامل (مثلاً Claude Code) با این توکن از مسیر /api/bridge/* و شیم گیت
    /bridge/git/<slug>.git کار می‌کند: push کد، بیلد/لاگ، exec تست، عیب‌یابی.
    قواعد امنیتی: مقدار توکن هرگز ذخیره نمی‌شود (فقط sha256)؛ نمایشِ کامل
    فقط یک‌بار در لحظهٔ ساخت؛ ابطال فوری؛ انقضای اختیاری؛ توکن مالکِ یک
    پروژه است و هیچ کوئری پل نمی‌تواند از این دانه بیرون برود.
    """
    __tablename__ = "agent_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(80), default="agent")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")   # برای نمایش: pst_xxxxxxxx
    created_at: Mapped[datetime] = mapped_column(DateTime, default=db_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
