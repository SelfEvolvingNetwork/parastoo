"""بذر جهانی مخزن تازه — یک اسکفولد واحد برای همهٔ پروژه‌ها (v0.5.2).

از v0.4.0 موتور بیلد فقط محتوای مخزن را می‌خواند (parastoo.yml یا تشخیص
خودکار از ریشه) — پس «انتخاب فناوری» هنگام ساخت پروژه بی‌معنا بود و حذف
شد. هر مخزن تازه با همین بذر جهانی متولد می‌شود: یک سایت استاتیک آمادهٔ
اجرا که اولین بیلد را همیشه سبز نگه می‌دارد (درس میدانی 2026-11-04:
مخزن خالی = بیلد مردودِ بی‌راهنما) و README آن پنج مسیر ممکن محتوا را
نشان می‌دهد — توسعه‌دهنده هر لحظه هر فایلی بگذارد، موتور همان را می‌خواند.

قرارداد ایمنی اعمال (پیاده‌سازی در services/projects.seed_scaffold):
  - کامیت اولیه فقط روی مخزن «خالی» اعمال می‌شود؛ محتوای کاربر هرگز بازنویسی نمی‌شود.
  - best-effort است: شکست آن هرگز ساخت پروژه را نمی‌شکند.
  - همهٔ فایل‌ها در «یک» کامیت تمیز با پیام COMMIT_MESSAGE ساخته می‌شوند.
جای‌گذارها: __NAME__ / __SLUG__ / __CLONE_URL__ (با replace اعمال می‌شود
نه format — تا آکولادهای CSS/JS محتوا آسیب نبینند).
"""

COMMIT_MESSAGE = "پرستو: ساختار اولیهٔ استاندارد پروژه"

GITIGNORE = """\
.DS_Store
Thumbs.db
*.log
.env
"""


README = """\
# __NAME__

پروژهٔ `__SLUG__` روی پرستو. محتوای این مخزن فقط «بذر شروع» است — آزادانه
همه‌اش را عوض کنید.

## چرخهٔ انتشار

```bash
git clone __CLONE_URL__
cd __SLUG__
# ... تغییرات شما ...
git add -A && git commit -m "تغییرات" && git push origin main
```

نام‌کاربری گیت دلخواه است و رمز = توکن عامل همین پروژه (پنل ← کارت
«پل عامل هوش مصنوعی»). با هر push به `main`: بیلد خودکار ← ایمیج جدید ←
به‌روزرسانی خودکار همهٔ سایت‌های این پروژه.

## پرستو در بیلد چه می‌خواند؟ (به همین ترتیب)

1. **parastoo.yml** در ریشه — سرویس‌های چندکانتینری (اپ + دیتابیس + …) را
   خودتان تعریف می‌کنید؛ هر ران‌تایم، بدون محدودیت.
2. **Dockerfile** در ریشه — بیلد با خودِ فایل شما (تنها قرارداد: گوش روی
   پورت 3000، متغیر محیطی `PORT`).
3. **package.json** در ریشه — اپ Node (اجرای `npm start` روی پورت 3000).
4. **requirements.txt** در ریشه — اپ پایتون (gunicorn + ورکر uvicorn روی
   پورت 3000؛ شیء اپ `app` در `app.py`).
5. **index.html** در ریشه — سایت استاتیک (nginx؛ بدون بیلد) — همین بذر.

هیچ‌کدام نبود؟ بیلد با پیام فارسیِ گویا راهنمایی می‌کند.

## اجرای محلی همین بذر

```bash
python3 -m http.server 3000
```

و باز کردن `http://localhost:3000`.
"""

INDEX_HTML = """\
<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__NAME__</title>
  <link rel="stylesheet" href="css/style.css">
</head>
<body>
  <main class="card">
    <h1>__NAME__</h1>
    <p>این سایت روی <strong>پرستو</strong> اجرا می‌شود.</p>
    <p>فایل‌های همین مخزن را تغییر دهید؛ با هر push به شاخهٔ <code>main</code> سایت به‌صورت خودکار بیلد و منتشر می‌شود.</p>
    <p id="note" class="muted"></p>
  </main>
  <script src="js/script.js"></script>
</body>
</html>
"""

STYLE_CSS = """\
/* __NAME__ — استایل‌ها؛ این فایل را آزادانه تغییر دهید. */
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: Vazirmatn, Tahoma, sans-serif;
  background: #f3f6fb;
  color: #1c2733;
  line-height: 1.9;
}
.card {
  background: #fff;
  max-width: 42rem;
  margin: 2rem 1rem;
  padding: 2.5rem;
  border-radius: 14px;
  box-shadow: 0 10px 30px rgba(28, 39, 51, .08);
}
h1 { margin-top: 0; color: #0f4c81; }
a { color: #0f4c81; }
code { background: #eef2f7; padding: .1rem .4rem; border-radius: 6px; }
.muted { color: #6b7a89; }
"""

SCRIPT_JS = """\
// __NAME__ — منطق سمت مرورگر؛ نقطهٔ شروع جاوااسکریپت شما.
document.addEventListener("DOMContentLoaded", () => {
  const note = document.getElementById("note");
  if (!note) return;
  note.textContent = "جاوااسکریپت فعال است ✔ — " + new Date().toLocaleDateString("fa-IR");
});
"""

# ترتیب فایل‌ها = ترتیب کامیت اولیه
SEED: dict[str, str] = {
    "README.md": README,
    "index.html": INDEX_HTML,
    "css/style.css": STYLE_CSS,
    "js/script.js": SCRIPT_JS,
    ".gitignore": GITIGNORE,
}


def files_for(project, clone_url: str) -> dict[str, str]:
    """فایل‌های بذر جهانی با جایگذاری شناسه‌های پروژه."""
    subs = {
        "__NAME__": project.name or project.slug,
        "__SLUG__": project.slug,
        "__CLONE_URL__": clone_url,
    }
    files: dict[str, str] = {}
    for path, content in SEED.items():
        for key, val in subs.items():
            content = content.replace(key, val)
        files[path] = content
    return files
