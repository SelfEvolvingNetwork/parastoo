# راهنمای توسعه پرستو (برای انسان و هوش مصنوعی)

این سند نقشه دقیق کد است. هنگام توسعه با AI، همین فایل را به آن بدهید.

## قواعد طلایی معماری

1. **هسته پرستو فقط «چسب» است.** گیت → Gitea API. اجرا/بیلد → Docker SDK. مسیریابی → لیبل‌های Traefik. هیچ ابزاری را بازنویسی نکنید.
2. **همگام و ساده.** SQLAlchemy همگام + ترد برای ورکرها. async فقط جایی که FastAPI الزام می‌کند (form parsing). تک‌سرور هدف است.
3. **Idempotent باشید.** هر عملیات گیت/داکر باید در اجرای مجدد خطا ندهد (خطای «از قبل وجود دارد» = موفق).
4. **خودترمیمی.** اگر گیتا در لحظه در دسترس نبود، رکورد در حالت `pending` می‌ماند و ورکر bootstrap بعداً می‌سازد. هرگز عملیات گیت را شرط ورود کاربر نکنید.
5. **همه پیکربندی از env** (`core/app/config.py`) — بدون فایل کانفیگ جدید.

## نقشه فایل‌ها

```
install.sh                 نصب تک‌فرمانی (دامنه، رمز، ترافیک، compose up، تایمر آپدیت خودکار)
uninstall.sh               حذف (+ پاک کردن تایمر آپدیت خودکار)
bin/parastoo               CLI مدیریت (status/logs/update/autoupdate/backup/reset/doctor)
docker-compose.yml         استک: postgres + gitea + core + traefik
postgres-init/01-gitea.sh  ساخت DB گیتا در اولین راه‌اندازی
deploy/templates/traefik.yml.tmpl  الگوی ترافیک (install.sh رندر می‌کند)
deploy/systemd/parastoo-autoupdate.{service,timer}  تایمر آپدیت خودکار (install.sh با مسیر واقعی رندر و فعال می‌کند)
scripts/auto-update.sh     آپدیت خودکار از گیت‌هاب (فلاک + بکاپ کد + تاریخچهٔ logs/updates.jsonl)
scripts/github_publish.sh   انتشار ریلیز: push main + ساخت ریلیز + آپلود zip + تأیید ۴گانه (GITHUB_TOKEN لازم دارد)

core/app/
  config.py     تنظیمات (دامنه‌ها، پلن‌ها/قیمت، کمیسیون، لیست فناوری‌ها، توکن نصب، DEPLOYED_COMMIT/AT، DEV_MODE/DEV_TOKEN)
  db.py         engine/session (SQLAlchemy همگام)
  models.py     User, Project, Build, Site, WalletTx, Setting
  security.py   scrypt، کوکی امضاشده (itsdangerous)، CSRF، slugify، گیت ویزارد نصب
  domains.py    دامنه‌های فعال (لایه پویا روی env — از جدول Setting با پیشوند domain_)
  traefik_ops.py  رندر پیکربندی استاتیک/داینامیک ترافیک + check_dns + apply_domain
  tpl.py        Jinja2 + فلش‌پیام‌های فارسی + فیلترهای قالب
  deps.py       get_current_user / csrf_guard / render / redirect
  gitea.py      کلاینت Gitea API + provision کاربر/مخزن + توکن سیستمی
  dockerops.py  بیلد/push/pull/اجرا/توقف کانتینر سایت + لیبل‌های ترافیک (پویا از domains)
  stacks.py     داکرفایل قالبی برای static/node/python/docker
  workers.py    سه ترد: bootstrap_loop، build_worker، billing_loop
  main.py       FastAPI app + healthz + /api/version (عمومی) + dashboard + صفحات خطا (خطای ۵۰۰ لاگ می‌شود)
  routes/
    setup.py        ویزارد سه‌گامی: توکن نصب ← مدیر ← دامنه/HTTPS (صفحه دامنه بعداً هم باز است)
    account.py      ثبت‌نام/ورود/خروج (+ provision حساب گیت)
    projects.py     ساخت/جزئیات/بیلد مجدد/مارکت/انتشار سایت
    sites.py        روشن/خاموش/حذف سایت
    marketplace.py  لیست + فعال‌سازی ایمیج (پرداخت از کیف پول)
    wallet.py       موجودی و تراکنش‌ها
    admin.py        آمار + شارژ دستی + مدیریت کاربران/سایت‌ها
    dev.py          حالت توسعه‌دهنده: /dev + /api/dev/report|logs|updates (خاموش = 404)
    hooks.py        وب‌هوک gitea (امضای HMAC → صف بیلد)
  web/templates/    قالب‌های فارسی RTL (base + 12 صفحه)
  web/static/style.css
```

## جریان‌های کلیدی

**ساخت پروژه:** POST /projects/new → ردیف Project (repo_status=pending) → تلاش فوری gitea.provision_project_repo → اگر گیتا پایین بود، ورکر bootstrap بعداً می‌سازد.

**بیلد:** gitea push → webhook POST /api/hooks/gitea/{id} (امضا) → Build(queued) → build_worker: clone از `http://user:token@gitea:3000/...` → Dockerfile (فایل کاربر یا stacks.py) → docker build → login+push به `127.0.0.1:3000/<user>/<slug>` (رجیستری داخلی گیتا؛ localhost برای دیمون ناامن-مجاز است) → به‌روزرسانی خودکار همه Siteهای آن پروژه.

**انتشار:** POST /projects/{slug}/publish → ردیف Site + dockerops.run_site (mem_limit/nano_cpus + لیبل‌های ترافیک Host(`slug.apps.domain`)) → ترافیک کشف خودکار.

**صورتحساب:** billing_loop هر ۶۰ ثانیه چک می‌کند؛ هر سایت روشن با `elapsed>=3600` هزینه پلن را کسر می‌کند؛ موجودی زیر DEBT_LIMIT → stop container + status=paused.

**مارکت‌پلیس:** POST /marketplace/{id}/use → کسر از خریدار، واریز ۹۰٪ به فروشنده، ساخت Site برای خریدار؛ pull ایمیج خصوصی سمت سرور با اعتبار فروشنده انجام می‌شود (خریدار به رجیستری دسترسی ندارد).

**دامنه (v0.2):** ویزارد /setup/domain (ادمین) → check.json برای رکوردها (JS زنده) → apply_domain در traefik_ops: نوشتن config/traefik.yml (استاتیک؛ در صورت تغییر، ری‌استارت کانتینر ترافیک از طریق docker.sock) + config/dynamic/routers.yml (روتینگ پنل/گیت؛ watch خودکار ترافیک) + ذخیره در Setting (domain_*) + بازسازی کانتینر همه سایت‌ها با زیردامنه جدید. روترهای لیبلیِ دامنه nip.io اولیه باقی می‌مانند → هیچ لحظه‌ای پنل بی‌مسیر نمی‌شود.

**ویزارد نصب (v0.2):** install.sh صفر سوال: nip.io خودکار + رمزهای تصادفی + PARASTOO_SETUP_TOKEN (چاپ در ترمینال). /setup بدون کوکی gate → فرم توکن؛ توکن درست → کوکی gate (itsdangerous، ۲ ساعت) → فرم مدیر → ریدایرکت /setup/domain. بعد از وجود مدیر، /setup برای همیشه بسته است.

**ریست کارخانه (v0.2.10):** `parastoo reset` و `uninstall.sh` (حالت ولوم‌ها) باید سه چیز را پاک کنند: کانتینرهای سایت (`parastoo-site-*` — خارج از استک compose و با restart=unless-stopped، پس `compose down` هرگز آنها را حذف نمی‌کند)، ولوم‌ها (`down -v`: postgres_data، gitea_data، gitea_indexer، parastoo_data) و ایمیج‌های پروژه‌ها (فیلتر `label=parastoo.slug`). ترتیب مهم است: اول کانتینرهای سایت، بعد down -v — وگرنه شبکهٔ apps آزاد نمی‌شود.

**آپدیت خودکار (v0.2.12):** نصب اولیه دستی است؛ بعد از آن تایمر systemd (`parastoo-autoupdate.timer`، هر ۶۰ ثانیه) `scripts/auto-update.sh` را صدا می‌زند. اسکریپت با `git ls-remote` شاخهٔ `PARASTOO_UPDATE_CHANNEL` را با کامیتِ فایل `.parastoo-deploy` مقایسه می‌کند؛ اگر جلو رفته باشد: دانلود تاربال (کدلود ← فالبک آرشیو، با پشتیبانی `PARASTOO_GH_PROXY`)، بکاپ کد در `backups/code-*.tar.gz`، جایگزینی امنِ فقط پوشه‌های کد (`.env`/`config/`/`backups/`/`logs/`/`.parastoo-deploy` دست نمی‌خورند)، نوشتن `PARASTOO_DEPLOYED_COMMIT/AT` در `.env` و `docker compose up -d --build`. نتیجه در `logs/updates.jsonl` ثبت و توسط `/api/dev/updates` خوانده می‌شود. کنترل: `parastoo autoupdate status|on|off|run`. حداقل فاصلهٔ بررسی با `PARASTOO_AUTO_UPDATE_INTERVAL` (پیش‌فرض ۱۲۰، حداقل ۶۰). سیکل توسعه: push به main ← تا ۲ دقیقه استقرار ← `curl /api/version` تأیید.

**حالت توسعه‌دهنده (v0.2.12):** `PARASTOO_DEV_MODE=true` در `.env` → صفحه `/dev` (ادمین) و `GET /api/dev/report|logs|updates` (ادمین یا هدر `X-Dev-Token` برابر `PARASTOO_DEV_TOKEN`). گزارش شامل نسخه/کامیت، سلامت، کانتینرها، آمار، بیلدهای ناموفق با انتهای لاگ، رینگ‌بافر لاگ (در routes/dev.py، آخرین ۱۰۰۰ رکورد) و تاریخچهٔ آپدیت (سوار از `./logs:/app/host-logs:ro`). وقتی خاموش است همهٔ مسیرها 404 می‌دهند (نه 403). خطاهای ۵۰۰ در main.py لاگ می‌شوند و در همان رینگ‌بافر می‌افتند.

## چگونه تست کنیم (بدون سرور)

```bash
cd core
pip install -r requirements.txt
PARASTOO_DISABLE_WORKERS=1 DATABASE_URL=sqlite:///./test.db uvicorn app.main:app --port 8000
```
داکر/گیتا در دسترس نیست؟ اشکالی ندارد — صفحه‌ها باز می‌شوند و رکوردها pending می‌مانند.

## ایده‌های گام بعد (اولویت پیشنهادی)

1. درگاه پرداخت (زرین‌پال/IDPay) + Webhook شارژ خودکار
2. رمزنگاری gitea_token/gitea_password در DB (مثلاً Fernet با SECRET_KEY)
3. دامنه اختصاصی per-site (رکورد CNAME + لیبل پویا)
4. بیلد صف با محدودیت هم‌زمانی قابل تنظیم + limit صف per-user
5. rootless buildkit برای ایزولاسیون بیلد
6. بکاپ زمان‌بندی‌شده داخل خود پرستو + بازگردانی
7. اکسپورت ایمیج (docker save) برای مارکت‌پلیس بین‌نمونه‌ای
8. اعلان تغییر ROOT_URL گیتا پس از تغییر دامنه (بازسازی کانتینر گیتا با env جدید) — فعلاً آدرس‌های UI از domains.py درست‌اند ولی ROOT_URL داخلی گیتا تا ری‌استارت compose قدیمی می‌ماند
