# راهنمای توسعه پرستو (برای انسان و هوش مصنوعی)

> مخاطب: کلاه معمار چرخه (I3) — قواعد طلایی و نقشهٔ فایل‌ها، پیش از هر تغییر کد

این سند نقشهٔ دقیق کد و قراردادهای معماری است. هنگام توسعه با AI، همین فایل را به آن بدهید.
**پروتکل اجرایی چرخهٔ توسعه–تست (گام‌به‌گام، معماری واحدِ خودتوسعه‌دهنده، راستی‌آزمایی زنده): `docs/AI_AGENT_LOOP.fa.md`** — این فایل نقشهٔ معماری است، آن فایل فرایند اجراست.

## قواعد طلایی معماری

1. **هسته پرستو فقط «چسب» است.** گیت → Gitea API. اجرا/بیلد → Docker SDK. مسیریابی → لیبل‌های Traefik. هیچ ابزاری را بازنویسی نکنید.
2. **همگام و ساده.** SQLAlchemy همگام + ترد برای ورکرها. async فقط جایی که FastAPI الزام می‌کند (form parsing). تک‌سرور هدف است.
3. **Idempotent باشید.** هر عملیات گیت/داکر باید در اجرای مجدد خطا ندهد (خطای «از قبل وجود دارد» = موفق).
4. **خودترمیمی.** اگر گیتا در لحظه در دسترس نبود، رکورد در حالت `pending` می‌ماند و ورکر bootstrap بعداً می‌سازد. هرگز عملیات گیت را شرط ورود کاربر نکنید.
5. **همه پیکربندی از env** (`core/app/config.py`) — بدون فایل کانفیگ جدید.
6. **لایه‌بندی:** مسیر HTTP → اعتبارسنجی (validation.py) → سرویس (services/) → زیرساخت (dockerops/gitea/traefik_ops/dnsops). منطق کسب‌وکار هرگز داخل routes/ نوشته نمی‌شود؛ هر قاعده فقط یک جا (مثلاً قیمت پلن فقط services/billing.py).
7. **بذر تک‌منبع:** محتوای اولیهٔ مخزن تازه فقط در `scaffolds.py` تعریف می‌شود (یک بذر جهانی — فناوری دیگر پرسیده نمی‌شود و در بیلد از محتوا خوانده می‌شود)؛ کامیت اولیه فقط روی مخزن «خالی» اعمال و best-effort است — محتوای کاربر هرگز بازنویسی نمی‌شود و شکست بذر هرگز ساخت پروژه را نمی‌شکند.

## نقشه فایل‌ها

```
install.sh                 نصب تک‌فرمانی (دامنه، رمز، ترافیک، compose up، تایمر tls-store)
uninstall.sh               حذف (+ پاک کردن تایمرها)
bin/parastoo               CLI مدیریت (status/logs/update/autoupdate/backup/reset/doctor)
docker-compose.yml         استک: postgres + gitea + core + traefik + dns
postgres-init/01-gitea.sh  ساخت DB گیتا در اولین راه‌اندازی
deploy/templates/          الگوهای ترافیک (install.sh رندر می‌کند؛ ensure_traefik_config در heal بازرندر)
deploy/systemd/            تایمر tls-store + تایمر autoupdate (پیش‌فرض مسلح از v0.5.13 — خاموشی: parastoo autoupdate off)
scripts/auto-update.sh     آپدیت خودکار از گیت‌هاب (فلاک + بکاپ کد + تاریخچهٔ updates.jsonl)
scripts/github_publish.sh  انتشار ریلیز: push main + ریلیز + آپلود zip (GITHUB_TOKEN لازم دارد)
scripts/tls-store.sh       فروشگاه ماندگار گواهی: save/restore/ensure/status
scripts/factory-reset.sh   ریست کارخانه مقاوم (بکاپ → حذف کامل → بازسازی → سلامت‌سنجی)

core/app/
  config.py     تنظیمات (VERSION، دامنه‌ها، پلن‌ها/قیمت، کمیسیون، توکن نصب، DEPLOYED_COMMIT/AT، DEV_TOKEN)
  db.py         engine/session (SQLAlchemy همگام) — create_all + auto_migrate (مهاجرت افزایشی خودکار در بوت)
  models.py     User, Project, Service, Site, Build, WalletTx, Payment, AgentToken, Setting, Heartbeat, Lead
  manifest.py   پارس امن parastoo.yml (ServiceSpec؛ لیست سفید سخت؛ detect_stack بدون مانیفست)
  validation.py SLUG_RE/EMAIL_RE/DOMAIN_RE + project_slug_error/site_slug_error
  security.py   scrypt، کوکی امضاشده (itsdangerous)، CSRF، slugify، گیت ویزارد نصب
  domains.py    دامنه‌های فعال (لایه پویا روی جدول Setting با پیشوند domain_)
  traefik_ops.py  رندر پیکربندی استاتیک/داینامیک ترافیک + check_dns + apply_domain + ensure_traefik_config
  tpl.py        Jinja2 + فلش‌پیام‌های فارسی + فیلترهای قالب
  devlog.py     رینگ‌بافر لاگ در حافظه (منبع /api/dev/logs)
  deps.py       resolve_user/get_current_user / csrf_guard / render / redirect / set_session
  gitea.py      کلاینت Gitea API + provision کاربر/مخزن + seed بذر اولیه + توکن سیستمی + ensure_sso_source (ثبت منبع OAuth2 با CLI رسمی)
  oauthidp.py   منطق OAuth2 Provider: کد یک‌بارمصرف ۱۲۰ثانیه‌ای + access/refresh امضاشده با itsdangerous (بدون حالت)
  dockerops.py  بیلد/push سرویس‌ها + deploy_project (کل-یا-هیچ) + شبکهٔ خصوصی پروژه + پین /etc/hosts + exec/logs
  stacks.py     داکرفایل قالبی برای static/node/python/docker + پیش‌بازرسی فایل کلید استک + دستور اجرای پیش‌فرض پایتون
  scaffolds.py  بذر جهانی مخزن تازه (یک قالب واحد: سایت استاتیک + README پنج‌مسیری) — فقط مخزن خالی
  workers.py    سه ترد: bootstrap_loop، build_worker، billing_loop (صورتحساب → services/billing)
  services/     منطق کسب‌وکار — بدون HTTP:
    billing.py    قیمت‌گذاری تک‌منبع: tier/price_hourly/hourly_cost(سرویس‌های running) + charge_running_services + گارد balance_ok
    business.py   کاتالوگ خدمات فروش‌پذیر (قیمت بازارمحور، بازنویسی ادمین) + پارامترهای کسب‌وکار (فرم ادمین) + تپش‌نگار پایداری + سربه‌سر
    projects.py   چرخهٔ پروژه: project_for (مالکیت/IDOR) + create_project + seed_scaffold + queue_build + delete_project
    sites.py      چرخهٔ سایت: site_for + publish_from_project/_deploy + start/stop/delete + live_statuses
    market.py     فعال‌سازی مارکت: activate_project (پرداخت + کمیسیون + پروژهٔ خریدار با کپی سرویس‌ها) + propagate
    payment.py    درگاه آزمایشی mock: start_charge/gateway_url/handle_callback (idempotent)
    bridge.py     پل عامل: mint/authenticate توکن (فقط sha256) + سقف نرخ + audit JSONL
    dnsops.py     کلاینت PowerDNS: create_zone(kind=base|customer)/upsert_record/sync_base_zone
    agentops.py   پل‌های dev: netcheck + readonly_sql (با audit)
    email.py      ارسال نامهٔ خروجی (v0.5.23): SMTP با env (PARASTOO_SMTP_*) + فالبک صندوق فایل DATA_DIR/mail/outbox.jsonl؛ best-effort — هیچ‌گاه مسیر HTTP را نمی‌شکند
    password_reset.py  یادآوری رمز: توکن یک‌بارمصرف ۶۰دقیقه (فقط sha256 ذخیره) + کول‌داون ۱۲۰ث + سقف ۳/ساعت + خودپاک‌ساز رکوردهای تمام‌شده
  main.py       کارخانهٔ create_app() + مونتاژ: میان‌افزار هدر امنیتی، استاتیک، روترها، هندلر خطا
  routes/
    setup.py        ویزارد سه‌گامی: توکن نصب ← مدیر ← دامنه/HTTPS
    auth.py         ثبت‌نام/ورود/خروج + ضد brute-force + یادآوری رمز (v0.5.23): /forgot-password و /reset-password — پاسخ یکسان بدون افشای وجود حساب؛ رمز هرگز ایمیل نمی‌شود، فقط لینک یک‌بارمصرف
    account.py      صفحهٔ /account + تغییر رمز
    gitsso.py       /git/sso — درِ ورود به جریان استاندارد OAuth2 گیتا (v0.5.7): مرورگر به /user/oauth2/parastoo گیتا می‌رود و هویت پنل با ACCOUNT_LINKING=auto به حساب گیتای هم‌ایمیل می‌خورد (جایگزین ترفند کوکی‌ریلی v0.5.5)
    oauthidp.py     /oauth/authorize|token|userinfo — تأمین‌کنندهٔ OAuth2 استاندارد پنل (RFC 6749) که گیتا با Auth Source رسمی خودش به آن وصل است؛ قرارداد پروفایل = client «gitea» در goth v1.80.0
    dashboard.py    / (خانه) و /dashboard
    health.py       healthz + /api/version (عمومی و سبک)
    agentdocs.py    /docs/agent — سند عمومی راهنمای عامل کاربر (تک‌منبع همیشه‌به‌روز؛ بدون رمز)
    projects.py     ساخت/جزئیات/بیلد مجدد/مارکت/انتشار سایت (نازک)
    sites.py        روشن/خاموش/حذف سایت (نازک)
    marketplace.py  لیست + فعال‌سازی (نازک؛ منطق در services/market.py)
    business.py     بازار و فروش: /pricing /order /status عمومی + /admin/business (نازک؛ منطق در services/business.py)
    wallet.py       موجودی و تراکنش‌ها
    payment.py      شارژ کیف پول + درگاه آزمایشی mock + callback
    bridge.py       پل عامل: /bridge/git/<slug>.git (شیم گیت) + /api/bridge/*
    tickets.py      تیکتینگ عضو‌به‌عضو: /api/tickets* + صفحات پنل /tickets* (صندوق/جزئیات/فرم — منطق در services/tickets.py)
    auth.py         ثبت‌نام/ورود/خروج + یادآوری رمز (v0.5.23): /forgot-password و /reset-password — منطق توکن در services/password_reset.py، نامه‌ها با services/email.py
    dns.py          UI + API مدیریت DNS ادمین
    admin.py        آمار + شارژ دستی + مدیریت کاربران/سایت‌ها
    dev.py          کنسول توسعه: /dev + /api/dev/* (همیشه روشن؛ ادمین یا X-Dev-Token)
    hooks.py        وب‌هوک gitea (امضای HMAC → صف بیلد)
    __init__.py     ROUTERS — ترتیب ثبت همهٔ روترها در اپ
  web/templates/    قالب‌های فارسی RTL
  web/static/style.css
```

## جریان‌های کلیدی

**ساخت پروژه:** POST /projects/new (نام + شناسه + توضیح — بدون پرسش فناوری) → ردیف Project → تلاش فوری gitea.provision_project_repo (اگر گیتا پایین بود، ورکر bootstrap بعداً می‌سازد) → کامیت اولیهٔ بذر جهانی (فقط مخزن خالی، best-effort).

**بیلد (سرویس‌محور):** push → webhook POST /api/hooks/gitea/{id} (امضا) → Build(queued) → build_worker: کلون → `manifest.parse_file` (بدون مانیفست: `detect_stack` از محتوای ریشه) → پیش‌بازرسی کلید استک → به‌ازای هر سرویس `build_and_push` به رجیستری گیتا → **بیلد «کل یا هیچ»:** شکست هر سرویس هیچ کانتینری را تغییر نمی‌دهد → `deploy_project` همهٔ سرویس‌ها را بازمی‌سازد (غیرعمومی‌ها اول) + شبکهٔ خصوصی `parastoo-proj-<id>` + alias شبکه = نام سرویس + پین تازهٔ `/etc/hosts`.

**انتشار (سایت):** POST /projects/{slug}/publish → گارد کیف پول (`balance_ok_total`) → ردیف Site (زیردامنه) → `_deploy`: کانتینرها با لیبل ترافیک همهٔ سایت‌ها (`traefik.docker.network` الزامی). **Site = روتر است، نه کانتینر؛** چند سایت = چند روتر روی سرویس عمومی. Site.status آینهٔ کانتینرهاست (`live_statuses`).

**صورتحساب:** billing_loop هر ۶۰ ثانیه؛ جمع سرویس‌های runningِ هر پروژه ساعتی کسر می‌شود (`charge_running_services`)؛ بدهی زیر DEBT_LIMIT → توقف کل پروژه (paused).

**مارکت‌پلیس:** POST /marketplace/{id}/use → کسر از خریدار، واریز ۹۰٪ به فروشنده، پروژهٔ مالک خریدار با کپی سرویس‌ها + استقرار روی شبکهٔ خودش؛ انتشار اپِ به‌روز به نصب‌ها خودکار (`_propagate_installs`). pull ایمیج خصوصی سمت سرور با اعتبار فروشنده.

**پل عامل پروژه:** توکن AgentToken (فقط sha256 در DB، نمایش یک‌بار، ابطال فوری) → شیم گیت `/bridge/git/<slug>.git` (پروتکل گیت هوشمند؛ مخزن مقصد همیشه از DB؛ اعتبار سمت‌سرور) + API `/api/bridge/*` (status/manifest/builds/log/exec/restart/env/audit) با سقف نرخ پنجره‌لغزان و audit JSONL. قرارداد: `docs/AGENT_BRIDGE.fa.md`. سند رو به عامل کاربر: `/docs/agent` (رندر از کد — با هر استقرار به‌روز) + متن آمادهٔ کپی در کارت پل صفحهٔ پروژه (توکن تازه در متن درج می‌شود).

**دامنه و DNS:** ویزارد /setup/domain (ادمین) → `apply_domain` (پیکربندی ترافیک + گواهی) + `provision_zone`/`sync_base_zone` (PowerDNS)؛ ورکر ۶۰ثانیه‌ای دامنهٔ معلق را خودگردان فعال می‌کند. حالت وایلدکارد: زون روی نیم‌سرور خودمان → سه گواهی DNS-01 برای کل استک (`*.<دامنه>` + apex+www + `*.apps.<دامنه>`). مدیریت رکورد: `services/dnsops.py` + `routes/dns.py`.

**گواهی ماندگار:** فروشگاه `/var/lib/parastoo/tls` (تایمر ۱۰دقیقه‌ای) — هر فعالیت ACME اول `ensure` بعد درخواست؛ فقط گواهی غایب از CA خواسته می‌شود.

**پرداخت:** `routes/payment.py` + `services/payment.py` — درگاه آزمایشی mock با callback رسیددار (idempotent با `pay_dup`)؛ درگاه واقعی مسیر رشد.

**ریست کارخانه:** `scripts/factory-reset.sh` — ترتیب الزامی: بکاپ ← کانتینرهای پروژه (داخل و خارج استک) ← `down -v --remove-orphans` با تأیید ← ایمیج‌ها با لیبل ← `up -d --build` ← پروب سلامت؛ گواهی‌ها از فروشگاه بازگردانی می‌شوند. کد و `.env` و پشتیبان‌ها می‌مانند.

**آپدیت (v0.5.13 — خودکار پیش‌فرض):** پوش به main → تایمر `parastoo-autoupdate` (tick هر ۶۰ث، حداقل فاصلهٔ بررسی ۱۲۰ث) → `scripts/auto-update.sh`: جایگزینی امنِ فقط پوشه‌های کد (`.env`/`config/`/`backups/`/`logs/` دست نمی‌خورد)، بکاپ کد، نتیجه در `logs/updates.jsonl`. مسیر صریح برای شتاب: `sudo parastoo update` یا lifecycle پل (`POST /api/admin/parastoo/update`). سیکل توسعه: push ← استقرار خودکار (معمولاً ≤۲ دقیقه) ← `curl /api/version` تأیید. خاموشی: `sudo parastoo autoupdate off`؛ نصب/حذف همچنان فقط صریح.

**کنسول توسعه (همیشه روشن — v0.5.29):** صفحهٔ `/dev` و `GET /api/dev/report|logs|updates|netcheck` + `POST /api/dev/sql` (ادمین یا `X-Dev-Token`) — بدون سوییچ حالت؛ معماری خودتوسعه‌دهنده (حلقه §۲).

## قرارداد ارتباط سرویس‌ها

**تصمیم در یک خط:** پروژه‌ها با API استاندارد قابل دسترس‌اند — چه از وب جهانی، چه از پروژه‌های دیگر؛ تنها درِ مشترک URL عمومی است: `https://<slug>.apps.<دامنه>`. فراخوانندهٔ وب و فراخوانندهٔ همسایه دقیقاً یک رفتار و یک احراز هویت می‌بینند؛ هیچ مسیر ویژهٔ «داخلی» وجود ندارد که جداگانه امنش کنیم.

### سطح A — درون-پروژه‌ای (کانتینر به کانتینر)

```yaml
# parastoo.yml
services:
  web:
    build: .
    port: 80
    public: true
    env: [DATABASE_URL=postgres://db:5432/app, CACHE_URL=redis://cache:6379/0]
  db:
    image: postgres:16-alpine
    volume: dbdata:/var/lib/postgresql/data
  cache:
    image: redis:7-alpine
```

1. **نام مقصد = نام سرویس در مانیفست** (`db`، `cache`) — نه نام کانتینر کامل.
2. **پورت مقصد = پورت مانیفست سرویس مقصد** — نه 3000 پیش‌فرض، نه پورت عمومی.
3. ترتیب راه‌اندازی با پرستو است (غیرعمومی‌ها اول)؛ اپ باید آماده‌شدن مقصد را با تلاش دوباره بپذیرد (`restart: unless-stopped`).
4. سرویس‌های غیرعمومی از بیرون و از پروژه‌های دیگر **هیچ‌وقت** قابل تماس نیستند — به‌ساختاری.
5. تأخیر این مسیر: ~۲ میلی‌ثانیه.

### سطح B — بین-پروژه‌ای و از وب جهانی (درِ واحد)

1. **احراز هویت انتها-به-انتها با اپ است** — «دسترس‌پذیری شبکه ≠ اعتماد». پلتفرم فقط حمل می‌کند (TLS وایلدکارد + مسیر پایدار).
2. **URL کامل بدهید، نه host:port دست‌ساز** — ترافیک خودش به پورت مانیفست سرویس عمومی می‌رود. برای فراخوانندهٔ روی همان سرور این hairpin داخلی است (اتصال تازه ~۰٫۱۲ث؛ با پین DNS گرم ~۰).
3. **پروژهٔ منتشرنشده نامرئی است** — نه از وب، نه از همسایه.

### DNS خوداتکا — پین نام‌های پلتفرم در /etc/hosts

هنگام هر استقرار، نام‌های زیرساخت (panel/git/base/www) + همهٔ سایت‌های منتشرشده در `/etc/hosts` کانتینرهای پروژه پین می‌شوند (`pin_host_entries`؛ IP: `PARASTOO_DNS_BIND_IP` با گارد محلی‌بودن). ویژگی‌های ایمن: زنجیرهٔ رزولوشن بیرونی دست‌نخورده؛ ورودی کهنه بی‌ضرر (404 ترافیک)؛ نام‌های تازه تا deploy بعدی از DNS بالادست — پین شتاب‌دهنده است نه پیش‌شرط.

### مرزها و ایده‌های ثبت‌شده

- فراخوانی بین-پروژه‌ای پرحجم از مسیر عمومی هزینهٔ TLS/hairpin دارد؛ اگر روزی workload واقعی طلب کرد: ریکورسور داخلی با فوروارد داینامیک زون‌ها، یا «شبکهٔ پیوند» opt-in با اتصال فقط سرویس عمومیِ مقصد (least-privilege). فعلاً هیچ‌کدام ساخته نشد: سادگی می‌ارزد.
- نسخه‌بندی/CORS/سقف نرخِ API خودِ اپ، کار اپ است؛ پلتفرم فقط «URL پایدار + TLS + درِ واحد» را تضمین می‌کند.

### چک‌های خودکار

- `e2e_live.py` با `PARASTOO_BRIDGE_TOKEN` + `PARASTOO_CROSS_TARGET`: چک `bridge-cross` (فراخوانی پروژهٔ دیگر از داخل کانتینر) و `bridge-hosts` (بودن پین) — بدون env ها SKIP تمیز.
- اسموک آفلاین: سازندهٔ نقشهٔ پین (`pin_host_entries`) — نام‌های زیرساخت + سایت‌ها + خاموشی قابلیت وقتی IP غایب است.

## چگونه تست کنیم (بدون سرور)

```bash
cd core
pip install -r requirements.txt
PARASTOO_DISABLE_WORKERS=1 DATABASE_URL=sqlite:///./test.db uvicorn app.main:app --port 8000
```

داکر/گیتا در دسترس نیست؟ اشکالی ندارد — صفحه‌ها باز می‌شوند و رکوردها pending می‌مانند.

## ایده‌های گام بعد (اولویت پیشنهادی)

1. درگاه پرداخت واقعی (زرین‌پال/IDPay) + وب‌هوک شارژ خودکار — جایگزین درگاه آزمایشی
2. رمزنگاری توکن‌های گیت در DB (مثلاً Fernet با SECRET_KEY)
3. دامنهٔ اختصاصی per-site (رکورد CNAME + روتر پویا) — اتصال دامنهٔ مشتری به سایت
4. بیلد صف با محدودیت هم‌زمانی قابل تنظیم + limit صف per-user
5. rootless buildkit برای ایزولاسیون بیلد
6. بکاپ زمان‌بندی‌شدهٔ داخل خود پرستو + بازگردانی
7. اکسپورت ایمیج (docker save) برای مارکت‌پلیس بین‌نمونه‌ای
8. جداکردن شبکهٔ روترینگ ترافیک از شبکهٔ مدیریتی هسته (دفاع در عمق — ببین AGENT_BRIDGE §مرزها)
