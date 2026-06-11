"""
backup_module.py — резервное копирование БД и персистентное состояние каталога в S3.

Используется при подготовке к релизу (см. config.json → "startup"):

  • `backup_database()` — снимает дамп текущей базы (`pg_dump -Fc`, custom-формат,
    восстанавливается `pg_restore`) и кладёт его в S3 под двумя ключами:
    датированным (`backups/db/app_db-<UTC timestamp>.dump`) и перезаписываемым
    `backups/db/latest.dump`. Вызывается при выключении backend (lifespan shutdown
    в main.py) — то есть каждый аккуратный останов оставляет свежую резервную копию.

  • `restore_database()` — обратная операция: восстанавливает БД из
    `backups/db/latest.dump` (`pg_restore --clean --if-exists`). Запускается на
    старте однократным режимом RESTORE_FROM_BACKUP=true (по умолчанию выключен);
    при успешном восстановлении сидирование пропускается, даже если включено.

  • `save_directory_snapshot(directory)` / `load_directory_snapshot(directory)` —
    onboarding-состояние каталога пользователей (`inSystem` / `employee_id` / `role`
    по логинам; пароли НЕ сохраняются). Каталог живёт в config.json и мутируется в
    памяти процесса (POST/DELETE /employees, маппинг ролей из AD) — без снимка эти
    изменения терялись при рестарте. Снимок пишется при каждом изменении каталога и
    при выключении; ЧИТАЕТСЯ только когда сидирование на старте отключено
    (seed_on_start=false): при включённом сидировании источник истины — config.json
    и детерминированный сид.

Все функции best-effort: при ненастроенном S3 или ошибке пишут в лог и не роняют
процесс. Модуль не импортирует core/main (никакого bootstrap на импорте) — его можно
использовать в тестах напрямую.
"""

import json
import os
import subprocess
from datetime import datetime, timezone

from src import s3_module

BACKUP_PREFIX = "backups"
DB_BACKUP_PREFIX = f"{BACKUP_PREFIX}/db"
DB_LATEST_KEY = f"{DB_BACKUP_PREFIX}/latest.dump"
DIRECTORY_STATE_KEY = f"{BACKUP_PREFIX}/state/user_directory.json"

# Поля каталога, составляющие onboarding-состояние (всё остальное — identity из AD
# или пароль mock-режима — в снимок не попадает).
_ONBOARDING_FIELDS = ("inSystem", "employee_id", "role")

PG_DUMP_TIMEOUT_SECONDS = 120
PG_RESTORE_TIMEOUT_SECONDS = 300


def _pg_conn_args() -> tuple[list, dict]:
    """Общие аргументы подключения для pg_dump/pg_restore + env с паролем."""
    args = [
        "-h", os.environ.get("DB_HOST", "localhost"),
        "-p", os.environ.get("DB_PORT", "5432"),
        "-U", os.environ.get("DB_USER", "postgres"),
        "-d", os.environ.get("DB_NAME", "postgres"),
    ]
    env = {**os.environ, "PGPASSWORD": os.environ.get("DB_PASSWORD", "")}
    return args, env


def backup_database(now=None):
    """Снять дамп БД (pg_dump custom-формат) и загрузить в S3.

    Возвращает ключ датированного дампа или None (S3 не настроен / pg_dump упал).
    """
    if not s3_module.is_configured():
        print("[backup] S3 is not configured — database backup skipped.")
        return None

    conn_args, env = _pg_conn_args()
    # custom-формат: сжат, восстанавливается pg_restore (в т.ч. выборочно)
    cmd = ["pg_dump", *conn_args, "-Fc"]
    try:
        res = subprocess.run(cmd, capture_output=True, env=env,
                             timeout=PG_DUMP_TIMEOUT_SECONDS)
    except FileNotFoundError:
        print("[backup] pg_dump is not installed in the image — database backup skipped.")
        return None
    except subprocess.TimeoutExpired:
        print(f"[backup] pg_dump timed out after {PG_DUMP_TIMEOUT_SECONDS}s.")
        return None
    if res.returncode != 0:
        print(f"[backup] pg_dump failed: {res.stderr.decode(errors='replace').strip()}")
        return None

    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    key = f"{DB_BACKUP_PREFIX}/{os.environ.get('DB_NAME', 'db')}-{ts}.dump"
    try:
        s3 = s3_module.get_s3()
        for k in (key, DB_LATEST_KEY):
            s3.put_object(Bucket=s3_module.S3_BUCKET, Key=k, Body=res.stdout,
                          ContentType="application/octet-stream")
    except Exception as e:
        print(f"[backup] uploading database dump to S3 failed: {e}")
        return None
    print(f"[backup] database dump uploaded to s3://{s3_module.S3_BUCKET}/{key} "
          f"({len(res.stdout)} bytes).")
    return key


def restore_database() -> bool:
    """Восстановить БД из последнего дампа в S3 (`backups/db/latest.dump`).

    Выполняет `pg_restore --clean --if-exists` (дамп читается с stdin) — схема и
    данные приводятся к состоянию бэкапа. Используется однократным режимом
    RESTORE_FROM_BACKUP=true на старте (см. core.py). Возвращает True при успехе.
    """
    if not s3_module.is_configured():
        print("[restore] S3 is not configured — restore skipped.")
        return False
    try:
        obj = s3_module.get_s3().get_object(Bucket=s3_module.S3_BUCKET, Key=DB_LATEST_KEY)
        dump = obj["Body"].read()
    except Exception as e:
        print(f"[restore] no database backup found at "
              f"s3://{s3_module.S3_BUCKET}/{DB_LATEST_KEY} ({e.__class__.__name__}: {e}).")
        return False

    conn_args, env = _pg_conn_args()
    cmd = ["pg_restore", *conn_args, "--clean", "--if-exists", "--no-owner"]
    try:
        res = subprocess.run(cmd, input=dump, capture_output=True, env=env,
                             timeout=PG_RESTORE_TIMEOUT_SECONDS)
    except FileNotFoundError:
        print("[restore] pg_restore is not installed in the image — restore skipped.")
        return False
    except subprocess.TimeoutExpired:
        print(f"[restore] pg_restore timed out after {PG_RESTORE_TIMEOUT_SECONDS}s.")
        return False
    if res.returncode != 0:
        print(f"[restore] pg_restore failed: {res.stderr.decode(errors='replace').strip()}")
        return False
    print(f"[restore] database restored from s3://{s3_module.S3_BUCKET}/{DB_LATEST_KEY} "
          f"({len(dump)} bytes).")
    return True


def save_directory_snapshot(directory: dict) -> bool:
    """Сохранить onboarding-состояние каталога (по логинам) в S3. Без паролей."""
    if not s3_module.is_configured():
        return False
    snapshot = {
        login: {f: entry[f] for f in _ONBOARDING_FIELDS if f in entry}
        for login, entry in (directory or {}).items()
    }
    try:
        s3_module.get_s3().put_object(
            Bucket=s3_module.S3_BUCKET,
            Key=DIRECTORY_STATE_KEY,
            Body=json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"[backup] saving directory snapshot failed: {e}")
        return False
    return True


def load_directory_snapshot(directory: dict) -> int:
    """Применить снимок onboarding-состояния к каталогу (merge по логинам).

    Для каждого логина из снимка onboarding-поля выставляются ровно в сохранённое
    состояние (отсутствующее в снимке поле удаляется — так «выключенный» сотрудник
    остаётся выключенным). Identity-поля (ФИО/отдел/должность/пароль) не трогаются;
    неизвестные каталогу логины пропускаются. Возвращает число применённых логинов.
    """
    if not s3_module.is_configured():
        print("[backup] S3 is not configured — directory snapshot not loaded.")
        return 0
    try:
        obj = s3_module.get_s3().get_object(Bucket=s3_module.S3_BUCKET,
                                            Key=DIRECTORY_STATE_KEY)
        snapshot = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        # Чаще всего — снимка ещё нет (первый запуск без сидирования). Не критично.
        print(f"[backup] directory snapshot not loaded ({e.__class__.__name__}: {e}).")
        return 0

    applied = 0
    for login, state in snapshot.items():
        entry = (directory or {}).get(login)
        if entry is None or not isinstance(state, dict):
            continue
        for f in _ONBOARDING_FIELDS:
            if f in state:
                entry[f] = state[f]
            else:
                entry.pop(f, None)
        applied += 1
    return applied
