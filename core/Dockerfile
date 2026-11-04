# پرستو — کنترل‌پلین
# تحریم‌ها: آینهٔ pip را می‌توان موقع بیلد با PARASTOO_PIP_INDEX_URL عوض کرد (docker-compose.yml)
FROM python:3.12-slim
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
