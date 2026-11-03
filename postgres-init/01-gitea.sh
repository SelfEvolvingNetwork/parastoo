#!/bin/bash
# ساخت دیتابیس گیتا در همان نمونه postgres پرستو (فقط در اولین راه‌اندازی اجرا می‌شود)
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE DATABASE gitea;
  CREATE USER gitea WITH PASSWORD '$GITEA_DB_PASSWORD';
  GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea;
  ALTER DATABASE gitea OWNER TO gitea;
EOSQL
