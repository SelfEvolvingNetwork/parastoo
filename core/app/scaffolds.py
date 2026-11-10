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
  <meta name="description" content="__NAME__ — این سایت در حال آماده‌سازی است و به‌زودی افتتاح می‌شود.">
  <meta property="og:title" content="__NAME__">
  <meta property="og:description" content="این سایت در حال آماده‌سازی است — به‌زودی افتتاح می‌شود.">
  <meta property="og:site_name" content="__NAME__">
  <title>__NAME__ — به‌زودی</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230f4c81'/%3E%3Ccircle cx='11' cy='16' r='4' fill='%23ffffff'/%3E%3Ccircle cx='21' cy='16' r='4' fill='%237fb3e0'/%3E%3C/svg%3E">
  <link rel="stylesheet" href="css/style.css">
</head>
<body>
  <main class="card">
    <span class="chip">در حال آماده‌سازی</span>
    <h1>__NAME__</h1>
    <p class="lead">این سایت به‌زودی افتتاح می‌شود.</p>
    <div class="dots" aria-hidden="true"><span></span><span></span><span></span></div>

    <details class="owner">
      <summary>راهنمای مالک سایت — انتشار اولین نسخه</summary>
      <ol>
        <li>محتوای واقعی سایت را در همین مخزن بگذارید (فایل <code>index.html</code> را جایگزین کنید یا از <code>parastoo.yml</code>/<code>Dockerfile</code>/<code>package.json</code> استفاده کنید).</li>
        <li>با هر <code>git push</code> به شاخهٔ <code>main</code>، سایت خودکار بیلد و منتشر می‌شود.</li>
        <li>راهنمای کامل در <code>README.md</code> مخزن؛ از پنل پرستو نیز بخش «پل عامل هوش مصنوعی» در دسترس است.</li>
      </ol>
      <p id="note" class="muted small"></p>
    </details>
  </main>
  <footer class="foot">
    <a href="https://panel.zeolitecenter.ir/pricing" rel="noopener">انتشار خودکار با <strong>پرستو</strong></a>
  </footer>
  <script src="js/script.js"></script>
</body>
</html>
"""

STYLE_CSS = """\
/* __NAME__ — استایل‌ها؛ این فایل را آزادانه تغییر دهید.
   بدون هیچ وابستگی بیرونی (فونت/آیکون/CDN) — در ایران هم همیشه کامل رندر می‌شود. */
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: Vazirmatn, Tahoma, "Segoe UI", sans-serif;
  background: linear-gradient(160deg, #eef3f9 0%, #e2ecf7 55%, #d8e6f4 100%);
  color: #16242f;
  line-height: 1.9;
  padding: 1.5rem;
}
.card {
  background: #fff;
  max-width: 34rem;
  width: 100%;
  padding: 2.5rem 2.2rem 2.2rem;
  border-radius: 16px;
  box-shadow: 0 14px 40px rgba(20, 40, 65, .10);
  text-align: center;
}
.chip {
  display: inline-block;
  background: #e7f0fb;
  color: #0f4c81;
  border: 1px solid #c6dcf2;
  border-radius: 999px;
  padding: .15rem .9rem;
  font-size: .82rem;
  margin-bottom: 1rem;
}
h1 { margin: .2rem 0 .4rem; color: #0f2b44; font-size: 1.7rem; }
.lead { color: #54677a; margin: 0 0 1.2rem; }
.dots { display: flex; gap: .5rem; justify-content: center; margin: 1rem 0 1.6rem; }
.dots span {
  width: 9px; height: 9px; border-radius: 50%;
  background: #0f4c81; opacity: .25;
  animation: pulse 1.4s infinite ease-in-out;
}
.dots span:nth-child(2) { animation-delay: .2s; }
.dots span:nth-child(3) { animation-delay: .4s; }
@keyframes pulse { 0%, 100% { opacity: .25; } 50% { opacity: .9; } }
@media (prefers-reduced-motion: reduce) { .dots span { animation: none; opacity: .55; } }
.owner {
  text-align: right;
  background: #f6f9fc;
  border: 1px solid #e2eaf3;
  border-radius: 12px;
  padding: .8rem 1.1rem;
  font-size: .9rem;
}
.owner summary {
  cursor: pointer;
  color: #0f4c81;
  font-weight: 600;
  user-select: none;
}
.owner ol { margin: .7rem 0 0; padding-inline-start: 1.2rem; }
.owner li { margin: .3rem 0; }
a { color: #0f4c81; }
code { background: #eef2f7; padding: .1rem .4rem; border-radius: 6px; font-size: .92em; direction: ltr; unicode-bidi: isolate; }
.muted { color: #6b7a89; }
.small { font-size: .8rem; }
.foot { position: fixed; bottom: 1rem; left: 0; right: 0; text-align: center; font-size: .8rem; }
.foot a { text-decoration: none; color: #5c7d90; }
"""

SCRIPT_JS = """\
// __NAME__ — منطق سمت مرورگر؛ نقطهٔ شروع جاوااسکریپت شما.
// پیام زیر فقط داخل «راهنمای مالک سایت» می‌آید — بازدیدکنندهٔ عادی نمی‌بیند.
document.addEventListener("DOMContentLoaded", () => {
  const note = document.getElementById("note");
  if (!note) return;
  note.textContent = "انتشار خودکار فعال است ✔ — آخرین بیلد: " +
    new Date().toLocaleDateString("fa-IR");
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
