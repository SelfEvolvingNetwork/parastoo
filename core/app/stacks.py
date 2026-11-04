"""قالب داکرفایل برای هر فناوری + تولید .dockerignore.

قاعده طلایی پرستو: اگر خود مخزن Dockerfile داشت از آن استفاده می‌شود؛
در غیر این صورت قالب امن و پیش‌بینی‌پذیر پرستو ساخته می‌شود.
قاعده مشترک: وب‌اپ باید روی پورت 3000 (متغیر محیطی PORT) گوش دهد.

تحریم‌ها: اگر PARASTOO_PIP_INDEX_URL یا PARASTOO_NPM_REGISTRY تنظیم شده باشد
موقع تولید داکرفایل، آینه جای پیش‌فرض رسمی می‌نشیند (فقط برای قالب‌های پرستو؛
داکرفایل خودِ کاربر دست‌نخورده می‌ماند).
"""

from .config import settings

DOCKERFILES: dict[str, str] = {
    "static": """\
# قالب پرستو — سایت استاتیک
FROM nginx:1.27-alpine
COPY . /usr/share/nginx/html
RUN rm -f /usr/share/nginx/html/Dockerfile
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
""",
    "node": """\
# قالب پرستو — اپ Node.js (گوش روی پورت 3000)
FROM node:20-alpine
WORKDIR /app
ENV PORT=3000 NODE_ENV=production
COPY package*.json ./
RUN npm install --omit=dev__NPM_OPTS__ || npm install__NPM_OPTS__
COPY . .
EXPOSE 3000
CMD ["npm", "start"]
""",
    "python": """\
# قالب پرستو — اپ پایتون (گوش روی پورت 3000)
FROM python:3.12-slim
WORKDIR /app
ENV PORT=3000 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir__PIP_OPTS__ -r requirements.txt || true
COPY . .
EXPOSE 3000
""",
    "docker": "",  # داکرفایل خود کاربر استفاده می‌شود
}

PYTHON_CMD_TEMPLATE = 'CMD ["sh", "-c", "{cmd}"]'

DOCKERIGNORE = """\
.git
.gitignore
node_modules
__pycache__
*.pyc
.env
.venv
venv
data
backups
*.md
"""


def _npm_opts() -> str:
    """گزینهٔ --registry برای npm فقط وقتی آینه تنظیم شده باشد."""
    reg = settings.NPM_REGISTRY
    return f" --registry={reg}" if reg else ""


def _pip_opts() -> str:
    """گزینهٔ --index-url برای pip فقط وقتی آینه تنظیم شده باشد."""
    idx = settings.PIP_INDEX_URL
    return f" --index-url={idx}" if idx else ""


def wants_own_dockerfile(stack: str) -> bool:
    return stack == "docker"


def dockerfile_for(stack: str, start_cmd: str | None = None) -> str | None:
    """محتوای داکرفایل قالبی؛ برای فناوری docker مقدار None (از فایل کاربر استفاده می‌شود)."""
    if stack == "docker":
        return None
    content = DOCKERFILES[stack]
    content = content.replace("__NPM_OPTS__", _npm_opts()).replace("__PIP_OPTS__", _pip_opts())
    if stack == "python":
        cmd = (start_cmd or "gunicorn -b 0.0.0.0:3000 app:app").strip()
        content += PYTHON_CMD_TEMPLATE.format(cmd=cmd) + "\n"
    return content


def default_port(stack: str) -> int:
    return 80 if stack == "static" else 3000
