"""اسکفولد استاندارد پروژه‌ها — ساختار اولیهٔ هر مدل + README قراردادی (v0.3.2).

قاعده: هر مدل باید «همین اول سبز» باشد — با حداقل فایل‌ها، بدون هیچ تغییری،
بیلد و انتشار موفق بدهد. رشد بعدی توسعه‌دهنده طبق «مسیر رشد» README می‌گذرد.
این از حادثهٔ میدانی 2026-11-04 آمده: مخزن خالی + استک ناهم‌خوان = خطای مبهم
«COPY failed: no source files were specified» برای کاربر تازه‌کار.

مدل‌های استاندارد پلتفرم — هر مدل یک قرارداد اجرایی متمایز دارد؛ مدل موازیِ
بی‌دلیل تعریف نمی‌کنیم (react/express/flask/django قرارداد اجرایی‌شان با یکی
از همین چهار یکی است و در «مسیر رشد» همان مدل نوشته می‌شود):
  static  — سایت/لندینگ بدون منطق سرور (nginx؛ بدون بیلد؛ ورودی index.html)
  node    — اپ JS روی PORT=3000 (npm start؛ اولیه بدون وابستگی → بیلد همیشه سبز)
  python  — API/بک‌اند FastAPI روی PORT=3000 (gunicorn + ورکر uvicorn)
  docker  — هر ران‌تایم دیگر با داکرفایل خود کاربر (پذیرش قرارداد پورت 3000)

قرارداد ایمنی اعمال (پیاده‌سازی در services/projects.seed_scaffold):
  - کامیت اولیه فقط روی مخزن «خالی» اعمال می‌شود؛ محتوای کاربر هرگز بازنویسی نمی‌شود.
  - best-effort است: شکست آن هرگز ساخت پروژه را نمی‌شکند.
  - همهٔ فایل‌ها در «یک» کامیت تمیز با پیام COMMIT_MESSAGE ساخته می‌شوند.
جای‌گذارها: __NAME__ / __SLUG__ / __CLONE_URL__ / __START_CMD__ / __STACK_LABEL__
(با replace اعمال می‌شود نه format — تا آکولادهای CSS/JS محتوا آسیب نبینند).
"""

from .config import settings

COMMIT_MESSAGE = "پرستو: ساختار اولیهٔ استاندارد پروژه"

# دستور اجرای پیش‌فرض هر مدل — فقط برای پروژه‌هایی که کاربر «دستور اجرا» نداده است.
# نکتهٔ فنی: FastAPI با ورکر syncِ گورنیکون (پیش‌فرض قدیمی «app:app») نمی‌سازد؛
# پروژه‌های پایتون جدید از v0.3.2 با ورکر uvicorn متولد می‌شوند.
# (پیش‌فرض قدیمی stacks.dockerfile_for برای مخازن قدیمی دست‌نخورده می‌ماند.)
DEFAULT_START_CMD: dict[str, str] = {
    "python": "gunicorn -b 0.0.0.0:3000 -k uvicorn.workers.UvicornWorker app:app",
}

GITIGNORE_NODE = """\
node_modules/
.env
npm-debug.log*
.DS_Store
"""

GITIGNORE_PY = """\
__pycache__/
*.pyc
.venv/
venv/
.env
.DS_Store
"""

GITIGNORE_PLAIN = """\
.DS_Store
Thumbs.db
*.log
.env
"""


def _readme(title: str, purpose: str, contract: list[str], local_run: str,
            growth: list[str], structure: str) -> str:
    """اسکلت مشترک README قراردادی هر مدل — «هر آنچه استاندارد است، در حد نیاز»."""
    if not isinstance(local_run, str):
        local_run = "\n".join(local_run)
    lines = [
        f"# {title}",
        "",
        "پروژهٔ `__SLUG__` روی پرستو — مدل «__STACK_LABEL__».",
        "این مخزن با یک کامیت اولیهٔ استاندارد شروع شده است؛ آزادانه تغییرش دهید.",
        "",
        "## این مدل برای چه کاری است؟",
        "",
        purpose,
        "",
        "## قرارداد پرستو (مهم)",
        "",
    ]
    lines += [f"- {c}" for c in contract]
    lines += [
        "",
        "## چرخهٔ انتشار",
        "",
        "```bash",
        "git clone __CLONE_URL__",
        "cd " + "__SLUG__",
        "# ... تغییرات شما ...",
        'git add -A && git commit -m "تغییرات" && git push origin main',
        "```",
        "",
        "با هر push به `main`: بیلد خودکار ← ایمیج جدید ← به‌روزرسانی خودکار همهٔ سایت‌های این پروژه.",
        "",
        "## اجرای محلی",
        "",
        local_run,
        "",
        "## مسیر رشد",
        "",
    ]
    lines += [f"- {g}" for g in growth]
    lines += [
        "",
        "## ساختار فعلی",
        "",
        "```",
        structure,
        "```",
        "",
    ]
    return "\n".join(lines)


SCAFFOLDS: dict[str, dict[str, str]] = {
    # ---------------------------------------------------------------- static
    "static": {
        "README.md": _readme(
            title="__NAME__ — مدل استاندارد «سایت استاتیک»",
            purpose=(
                "سایت‌ها و لندینگ‌پیج‌هایی که منطق سمت سرور ندارند: HTML/CSS/JS خالص.\n"
                "سبک‌ترین و سریع‌ترین مدل پرستو — مرحلهٔ بیلد ندارد؛ nginx فایل‌ها را همان‌طور که هست سرو می‌کند."
            ),
            contract=[
                "نقطهٔ ورود سایت: `index.html` در ریشهٔ مخزن.",
                "هر فایل کنار `index.html` با همان مسیر سرو می‌شود (`css/style.css` ← `/css/style.css`).",
                "به کانتینر دستور اجرا و پورت نمی‌دهید — nginx خودش روی 80 گوش می‌دهد و ترافیک همان‌جا می‌رود.",
            ],
            local_run=(
                "هیچ نصبی لازم نیست: `python3 -m http.server 3000` در ریشهٔ مخزن و باز کردن "
                "`http://localhost:3000` (یا هر سرور فایل سادهٔ دیگر)."
            ),
            growth=[
                "چند صفحه؟ فایل‌های HTML جدید اضافه کنید و به هم لینک بدهید.",
                "منطق سمت سرور لازم شد؟ فایل parastoo.yml به مخزن اضافه کنید یا یک پروژهٔ جدید با قالب Node/Python بسازید (محدودیت فناوری در پرستو وجود ندارد).",
                "فریم‌ورکی مثل React/Vue که مرحلهٔ build دارد؟ مدل «داکرفایل اختصاصی»: در Dockerfile خودتان build کنید و خروجی `dist/` را با nginx سرو کنید.",
            ],
            structure=(
                "index.html        نقطهٔ ورود سایت\ncss/style.css     استایل‌ها\njs/script.js      جاوااسکریپت مرورگر"
            ),
        ),
        "index.html": """\
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
""",
        "css/style.css": """\
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
""",
        "js/script.js": """\
// __NAME__ — منطق سمت مرورگر؛ نقطهٔ شروع جاوااسکریپت شما.
document.addEventListener("DOMContentLoaded", () => {
  const note = document.getElementById("note");
  if (!note) return;
  note.textContent = "جاوااسکریپت فعال است ✔ — " + new Date().toLocaleDateString("fa-IR");
});
""",
        ".gitignore": GITIGNORE_PLAIN,
    },

    # ---------------------------------------------------------------- node
    "node": {
        "README.md": _readme(
            title="__NAME__ — مدل استاندارد «Node.js»",
            purpose=(
                "اپ‌های جاوااسکریپت سمت سرور: API، سایت داینامیک، WebSocket و real-time.\n"
                "قالب اولیه عمداً «بدون وابستگی» است (فقط ماژول داخلی http) تا اولین بیلد همیشه سبز باشد — "
                "حتی اگر رجیستری npm موقتاً در دسترس نباشد."
            ),
            contract=[
                "اپ باید روی پورت 3000 گوش دهد — متغیر محیطی `PORT` به کانتینر تزریق می‌شود؛ همیشه از `process.env.PORT` بخوانید.",
                "شروع اپ با `npm start` است (در package.json تعریف شده) — این دستور را نگه دارید.",
                "فایل کلید مدل: `package.json` در ریشهٔ مخزن — بدون آن بیلد معنا ندارد.",
                "اگر در ریشهٔ مخزن `Dockerfile` خودتان را بگذارید، پرستو از قالب صرف‌نظر و از فایل شما استفاده می‌کند (پورت 3000 هنوز الزامی است).",
            ],
            local_run=(
                "`npm start` (یا `node server.js`) و باز کردن `http://localhost:3000`.\n"
                "نیاز به Node 20 دارید؛ وابستگی جدید: `npm install <package>`"
            ),
            growth=[
                "Express: `npm install express` و جایگزینی server.js با مثال کوتاه پایین.",
                "```js",
                "  const express = require(\"express\");",
                "  const app = express();",
                "  app.get(\"/\", (req, res) => res.send(\"سلام از __SLUG__\"));",
                "  app.listen(process.env.PORT || 3000);",
                "```",
                "وابستگی‌های production را با `npm install <pkg>` نصب کنید (به package.json اضافه می‌شود و در بیلد نصب می‌شوند).",
                "دیتابیس؟ از متغیر محیطی برای آدرس اتصال استفاده کنید؛ مقادیر محرمانه را commit نکنید (.env در .gitignore است).",
            ],
            structure="server.js          نقطهٔ ورود اپ (روی PORT گوش می‌دهد)\npackage.json       شناسنامه و اسکریپت‌ها (npm start)",
        ),
        "server.js": """\
// __NAME__ — مدل استاندارد Node پرستو.
// قرارداد: اپ باید روی پورت 3000 (متغیر محیطی PORT) گوش دهد.
// این قالب عمداً بدون وابستگی است تا اولین بیلد همیشه سبز باشد.
const http = require("http");

const PORT = Number(process.env.PORT) || 3000;

const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(`<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>__NAME__</title></head>
<body style="font-family:Tahoma,sans-serif;max-width:40rem;margin:4rem auto;line-height:2">
  <h1>__NAME__</h1>
  <p>اپ Node شما روی پرستو اجرا شد ✔ — مسیر درخواست: <code>${req.url}</code></p>
  <p>از اینجا رشد کنید: Express، WebSocket، دیتابیس… (راهنما در README)</p>
</body>
</html>`);
});

server.listen(PORT, () => {
  console.log("__SLUG__ listening on port " + PORT);
});
""",
        "package.json": """\
{
  "name": "__SLUG__",
  "version": "1.0.0",
  "private": true,
  "description": "__NAME__ — اپ Node روی پرستو",
  "main": "server.js",
  "scripts": {
    "start": "node server.js"
  },
  "engines": {
    "node": ">=20"
  }
}
""",
        ".gitignore": GITIGNORE_NODE,
    },

    # ---------------------------------------------------------------- python
    "python": {
        "README.md": _readme(
            title="__NAME__ — مدل استاندارد «Python (FastAPI)»",
            purpose=(
                "APIها و بک‌اندهای پایتون با FastAPI — استاندارد مدرن پایتون برای وب.\n"
                "همان فریم‌وری که خود پرستو با آن نوشته شده؛ مستندات تعاملی خودکار در مسیر `/docs` دارد."
            ),
            contract=[
                "اپ باید روی پورت 3000 گوش دهد — متغیر محیطی `PORT` به کانتینر تزریق می‌شود.",
                "فایل کلید مدل: `requirements.txt` در ریشهٔ مخزن (وابستگی‌ها از آن نصب می‌شوند).",
                "شیء اپ باید `app` نام داشته باشد و در `app.py` باشد (یا دستور اجرا را هنگام ساخت پروژه عوض کنید).",
                "اجرای روی پرستو با این دستور انجام می‌شود:",
                "  `__START_CMD__`",
                "اگر در ریشهٔ مخزن `Dockerfile` خودتان را بگذارید، پرستو از فایل شما استفاده می‌کند (پورت 3000 هنوز الزامی است).",
            ],
            local_run=(
                "```bash",
                "  python3 -m venv .venv && source .venv/bin/activate",
                "  pip install -r requirements.txt",
                "  uvicorn app:app --reload --port 3000",
                "```",
                "و باز کردن `http://localhost:3000` (مستندات تعاملی: `/docs`).",
            ),
            growth=[
                "مسیر جدید: در app.py یک `@app.get(\"/...\")` اضافه کنید.",
                "وابستگی جدید: به requirements.txt اضافه‌اش کنید (در بیلد بعدی نصب می‌شود).",
                "دیتابیس؟ SQLAlchemy + یک فایل models.py (نقشهٔ خود پرستو در docs/DEVELOPMENT.md مخزن پرستو الگوی خوبی است).",
                "Flask یا Django ترجیح می‌دهید؟ همین مدل می‌ماند — فقط دستور اجرا را هنگام ساخت پروژه متناسب کنید (مثلاً `gunicorn -b 0.0.0.0:3000 app:app`).",
            ],
            structure="app.py              نقطهٔ ورود (شیء FastAPI به نام app)\nrequirements.txt    وابستگی‌ها",
        ),
        "app.py": """\
\"\"\"__NAME__ — مدل استاندارد Python پرستو (FastAPI).

قرارداد: اپ باید روی پورت 3000 (متغیر محیطی PORT) گوش دهد.
اجرای روی پرستو (خودکار):
    __START_CMD__
اجرای محلی:
    uvicorn app:app --reload --port 3000
\"\"\"
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="__NAME__")

PAGE = \"\"\"<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>__NAME__</title></head>
<body style="font-family:Tahoma,sans-serif;max-width:40rem;margin:4rem auto;line-height:2">
  <h1>__NAME__</h1>
  <p>اپ FastAPI شما روی پرستو اجرا شد ✔</p>
  <p>مسیرهای فعلی: <code>/</code> و <code>/api/hello</code> و <code>/docs</code></p>
</body>
</html>\"\"\"


@app.get("/")
def home():
    return HTMLResponse(PAGE)


@app.get("/api/hello")
def hello():
    return {"message": "سلام از __SLUG__!", "port": os.environ.get("PORT", "3000")}
""",
        "requirements.txt": """\
# __NAME__ — وابستگی‌ها؛ هر وابستگی جدید را اینجا اضافه کنید (در بیلد نصب می‌شود).
fastapi>=0.110
uvicorn>=0.29
gunicorn>=21.2
""",
        ".gitignore": GITIGNORE_PY,
    },

    # ---------------------------------------------------------------- docker
    "docker": {
        "README.md": _readme(
            title="__NAME__ — مدل استاندارد «داکرفایل اختصاصی»",
            purpose=(
                "وقتی هیچ‌کدام از سه مدل آماده جواب نمی‌دهد: هر زبان، هر ران‌تایم، هر ابزار بیلد — "
                "کنترل کامل با شما، در ازای این‌که خودتان Dockerfile را نگه دارید."
            ),
            contract=[
                "تنها قرارداد سخت: برنامهٔ نهایی باید روی پورت 3000 گوش دهد (متغیر محیطی `PORT` تزریق می‌شود).",
                "فایل کلید مدل: `Dockerfile` در ریشهٔ مخزن — بدون آن بیلد خطای واضح می‌دهد.",
                "پرستو کنار بیلد یک `.dockerignore` استاندارد هم می‌سازد (.git، .env، node_modules و…).",
                "برای اپ‌های استاتیک ساده مدل «استاتیک» ساده‌تر است؛ این مدل برای وقتی است که مرحلهٔ بیلد دارید.",
            ],
            local_run=(
                "```bash",
                "  docker build -t __SLUG__ .",
                "  docker run -p 3000:3000 __SLUG__",
                "```",
                "و باز کردن `http://localhost:3000`.",
            ),
            growth=[
                "نمونهٔ فعلی فقط یک سایت استاتیک روی nginx است — هر چیزی جایگزینش کنید.",
                "فریم‌ورک فرانت‌اند با مرحلهٔ build؟ الگوی چندمرحله‌ای: مرحلهٔ اول build (node)، مرحلهٔ دوم سرو خروجی با nginx.",
                "هر زبان دیگری (Go، Rust، PHP، …) — فقط پورت 3000 را رعایت کنید.",
            ],
            structure="Dockerfile         دستورالعمل بیلد شما (نمونهٔ کارا)\nindex.html         محتوای نمونه",
        ),
        "Dockerfile": """\
# __NAME__ — مدل «داکرفایل اختصاصی» پرستو؛ کنترل کامل با شما.
# تنها قرارداد: برنامهٔ نهایی باید روی پورت 3000 گوش دهد (متغیر محیطی PORT).
# این نمونهٔ کارا، یک سایت استاتیک را روی پورت 3000 سرو می‌کند — هر چیزی جایگزینش کنید.
FROM nginx:1.27-alpine
COPY index.html /usr/share/nginx/html/index.html
RUN sed -i 's/listen       80;/listen       3000;/' /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
""",
        "index.html": """\
<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><title>__NAME__</title></head>
<body style="font-family:Tahoma,sans-serif;max-width:40rem;margin:4rem auto;line-height:2">
  <h1>__NAME__</h1>
  <p>این نمونهٔ مدل «داکرفایل اختصاصی» پرستو است — Dockerfile همین مخزن، بیلد و انتشار خودکار.</p>
</body>
</html>
""",
        ".gitignore": GITIGNORE_PLAIN,
    },
}


def files_for(project, clone_url: str) -> dict[str, str] | None:
    """فایل‌های اسکفولد پروژه با جایگذاری شناسه‌ها؛ مدل ناشناخته → None."""
    base = SCAFFOLDS.get(project.stack)
    if not base:
        return None
    label = (settings.STACKS.get(project.stack) or {}).get("label", project.stack)
    subs = {
        "__NAME__": project.name or project.slug,
        "__SLUG__": project.slug,
        "__CLONE_URL__": clone_url,
        "__START_CMD__": DEFAULT_START_CMD.get(project.stack, ""),
        "__STACK_LABEL__": label,
    }
    files: dict[str, str] = {}
    for path, content in base.items():
        for key, val in subs.items():
            content = content.replace(key, val)
        files[path] = content
    return files
