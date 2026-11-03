# نصب پرستو در ایران — راهنمای تحریم‌ها

سرورهای ایرانی با محدودیت‌های جدی طرف هستند. پرستو از نسخهٔ 0.2.1 سه لایهٔ دفاعی داخلی دارد که **بدون هیچ سوالی** فعال می‌شوند؛ این سند توضیح می‌دهد دقیقاً چه چیزی تحریم است، پرستو چه می‌کند و اگر هیچ‌کدام جواب نداد چطور ۱۰۰٪ آفلاین نصب کنید.

## ۱) دقیقاً چه چیزهایی تحریم است؟

| مورد | آدرس | وضعیت از ایران |
|---|---|---|
| نصب رسمی داکر | `get.docker.com` ، `download.docker.com` | **مسدود (403)** |
| pull ایمیج از Docker Hub | `registry-1.docker.io` | **مسدود (timeout/403)** |
| مخازن اوبونتو | `archive.ubuntu.com` و آینه‌های داخلی | باز — فقط کند |
| PyPI (نصب پکیج پایتون) | `pypi.org` | باز — گاهی کند |
| npm | `registry.npmjs.org` | باز — گاهی کند |
| Let's Encrypt (گواهی HTTPS) | `acme-v02.api.letsencrypt.org` | باز |
| GitHub | `github.com` | باز (فقط کند/ناپایدار گاهی) |
| `nip.io` (دامنهٔ موقت) | DNS | باز |

نتیجهٔ عملی: چیزی که واقعاً می‌شکند **نصب داکر** و **پول ایمیج‌ها** است — نه کد پرستو، نه HTTPS، نه پایتون/نود.

### گرفتن خود پرستو از گیت‌هاب (ایران)

`github.com` از ایران باز است؛ اگر گاهی کند بود یا `raw.githubusercontent.com` جواب نداد:

```bash
# مسیر ۱) همان دستور تک‌خطی — bootstrap.sh خودش سه مسیر را امتحان می‌کند
curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash

# مسیر ۲) پروکسی گیت‌هاب (فقط برای دانلود؛ اسکریپت را قبل از اجرا می‌توانید ببینید)
curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash

# مسیر ۳) کلون دستی و اجرای محلی
git clone https://github.com/SelfEvolvingNetwork/parastoo.git && cd parastoo && sudo bash setup.sh

# مسیر ۴) کاملاً دستی: zip را از صفحهٔ ریلیزها در مرورگر بگیرید، به سرور ببرید:
sudo bash setup.sh parastoo-v0.2.1.zip
```


## ۲) لایهٔ ۱ — نصب داکر از مخازن اوبونتو (خودکار)

`install.sh` اول مسیر رسمی (`get.docker.com`) را امتحان می‌کند؛ اگر مسدود بود (سرور ایران) خودش از مخازن خود اوبونتو نصب می‌کند:

```bash
apt-get install -y docker.io docker-compose-v2
```

مخازن اوبونتو (و آینه‌های ایرانی مثل ir.archive.ubuntu.com) تحریم نیستند، پس این مسیر همیشه از داخل ایران کار می‌کند. اگر نسخهٔ اوبونتوی شما خیلی قدیمی است و `docker-compose-v2` ندارد، دستی نصب کنید:

```bash
sudo apt-get install -y docker.io docker-compose-v2
docker compose version   # باید ورژن چاپ شود
```

## ۳) لایهٔ ۲ — آینه‌های ایرانی Docker Hub (خودکار، با تست زنده‌بودن)

سرویس‌های ایرانیِ آینهٔ داکر (docker.ir، آروان‌کلاد، نوین‌کلاد و ...) دقیقاً همین مشکل را حل می‌کنند: آن‌ها کپی از Docker Hub داخل ایران دارند و فقط پیکربندی `daemon.json` می‌خواهند — بدون ثبت‌نام و توکن. پرستو این پیکربندی را **خودش انجام می‌دهد**، نه با یک لیست ثابت، بلکه موقع نصب:

1. پروب سریع به `registry-1.docker.io/v2/` (پاسخ 401 = در دسترس). اگر برقرار بود، کاری نمی‌کند.
2. اگر مسدود بود، همهٔ کاندیدها (`deploy/mirrors.txt`) را **از داخل همین سرور** تست می‌کند (`scripts/check-mirrors.sh` — endpoint استاندارد `/v2/`، حذف مرده‌ها و کندتر از ۴ ثانیه).
3. فقط زنده‌ها را به ترتیب سرعت در `/etc/docker/daemon.json` می‌نویسد و داکر را ری‌استارت می‌کند.
4. تأیید نهایی با یک pull واقعی (`alpine:3.20` — همان ایمیجی که دستور backup هم لازم دارد).

لیست پیش‌فرض (تست‌شده؛ آینه‌های مرده مثل runflare و iranserver حذف شده‌اند):

```json
{
  "registry-mirrors": [
    "https://docker.arvancloud.ir",
    "https://docker.novin.cloud",
    "https://registry.docker.ir",
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run"
  ]
}
```

- داکر ترتیب را رعایت می‌کند؛ اگر آینه‌ای از کار افتاد سراغ بعدی می‌رود و در نهایت خود Hub را هم امتحان می‌کند.
- این تنظیم فقط روی ایمیج‌های Docker Hub (`docker.io`) اثر دارد — که همهٔ ایمیج‌های پرستو همین‌اند (postgres، gitea، traefik، python، node، nginx).
- چرا تست از خود سرور؟ چون بعضی آینه‌ها از خارج ایران بی‌پاسخ‌اند و بعضی ممکن است فقط برای IP ایران باز باشند — تنها معیار درست، تست روی خود سرور است.
- **تست/عیب‌یابی دستی هر وقت خواستید:** `bash scripts/check-mirrors.sh` (نمایش زنده‌ها به ترتیب سرعت) — بعد `sudo systemctl restart docker`.
- اگر آینهٔ خودتان دارید، به `deploy/mirrors.txt` اضافه کنید و install.sh را دوباره اجرا کنید، یا یک‌خطی:

```bash
sudo PARASTOO_REGISTRY_MIRRORS="https://docker.arvancloud.ir https://registry.docker.ir" bash install.sh
```

بررسی اینکه آینه‌ها نشسته‌اند:

```bash
docker info | grep -A 8 "Registry Mirrors"
```

## ۴) لایهٔ ۳ — بستهٔ آفلاین (مطمئن‌ترین راه؛ بدون هیچ اتصال خارجی)

اگر آینه‌ها هم قطع بودند یا نمی‌خواهید به هیچ سرویس ثالثی وابسته باشید:

**قدم ۱ — روی یک ماشین بدون تحریم** (لپ‌تاپ شخصی با تغییر IP، سرور خارج، یا GitHub Actions):

```bash
bash scripts/fetch-images.sh
# خروجی: offline-images/parastoo-images.tar.gz  (حدود ۱ تا ۱.۵ گیگابایت)
```

**قدم ۲ — انتقال به سرور ایران، کنار پکیج پرستو:**

```bash
scp offline-images/ user@server:/path/to/parastoo/
```

**قدم ۳ — نصب مجدد روی سرور:**

```bash
sudo bash install.sh
```

اسکریپت پوشهٔ `offline-images/` را خودش پیدا می‌کند، همهٔ ایمیج‌ها را با `docker load` بارگذاری می‌کند و دیگر **هیچ pull از اینترنت نمی‌زند**. بیلد هسته هم آفلاین انجام می‌شود چون ایمیج پایهٔ `python:3.12-slim` داخل بسته است.

## ۵) بیلدهای کاربران — آینهٔ pip/npm

سایت‌های کاربران هم موقع بیلد به پکیج‌منیجر نیاز دارند (PyPI و npm از ایران معمولاً بازند، ولی اگر کند بود یا خواستید ترافیک داخلی بماند)، در `.env`:

```env
PARASTOO_PIP_INDEX_URL=https://pypi.org/simple     # یا آینهٔ داخلی
PARASTOO_NPM_REGISTRY=https://registry.npmjs.org   # یا آینهٔ داخلی
```

و بعد `parastoo update`. این مقادیر فقط روی **قالب‌های تولیدی پرستو** (پایتون/نود) اثر می‌گذارند؛ داکرفایل خودِ کاربر دست‌نخورده می‌ماند — اگر کسی در داکرفایلش از رجیستری دیگری (مثل `ghcr.io`) استفاده کند، آینهٔ Hub شاملش نمی‌شود و باید آن ایمیج را با `docker load` آفلاین بدهید.

## ۶) عیب‌یابی سریع

| خطا | علت | راه‌حل |
|---|---|---|
| `get.docker.com` در نصب fail شد | طبیعی در ایران | کاری نکنید؛ خودش از مخازن اوبونتو ادامه می‌دهد |
| `toomanyrequests` / timeout موقع pull | Hub مسدود یا آینهٔ مرده | لایهٔ ۲ (آینه) یا لایهٔ ۳ (آفلاین) |
| بیلد هسته خطای pip می‌دهد | pypi کند | `PARASTOO_PIP_INDEX_URL` در .env + `parastoo update` |
| بیلد نود خطای npm می‌دهد | registry کند | `PARASTOO_NPM_REGISTRY` در .env + `parastoo update` |
| `docker info` آینه‌ها را نشان نمی‌دهد | JSON خراب یا ری‌استارت نشده | `sudo systemctl restart docker` و اعتبار JSON |
| ویزارد دامنه گواهی نگرفت | نادر است؛ LE از ایران باز است | لاگ: `parastoo logs traefik` |

## ۷) چک‌لیست نصب در ایران (خلاصه)

```bash
unzip parastoo-v0.2.1.zip && cd parastoo
sudo bash install.sh          # خودش مسیر تحریم را تشخیص می‌دهد و دور می‌زند
# → آدرس پنل + توکن نصب چاپ می‌شود؛ بقیه در مرورگر
```

اگر بعد از install.sh خطای pull دیدید، فقط یک‌بار لایهٔ ۳ (بستهٔ آفلاین) را اجرا کنید — تمام.
