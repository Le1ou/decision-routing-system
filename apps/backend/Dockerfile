FROM python:3.11-slim

WORKDIR /app

# postgresql-client-15 даёт pg_dump/pg_restore для резервного копирования БД в S3
# (см. src/backup_module.py). Версия клиента ДОЛЖНА совпадать с мажорной версией
# сервера (postgres:15 в compose): клиент 17 из стандартного репозитория пишет в
# дамп параметры, которые сервер 15 не понимает (restore падает) — поэтому ставим
# из официального репозитория PGDG ровно 15-ю версию.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-15 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "3000"]
