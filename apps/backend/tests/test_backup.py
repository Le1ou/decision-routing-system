"""
Tests for backup_module: database dump to S3 (pg_dump) and the user-directory
onboarding snapshot (save/load round-trip).

Runs inside the backend container (same harness as test_events.py): S3 is the
local MinIO from the compose stack, pg_dump comes from postgresql-client in the
image. The functions are called directly — no HTTP involved.
"""

import json
import uuid

import pytest

from src import backup_module, s3_module
from src.application_module import PgDbOperator


@pytest.fixture(scope="module")
def sysdb():
    return PgDbOperator("postgres", "postgres")


def test_database_backup_uploaded_to_s3():
    key = backup_module.backup_database()
    assert key, "pg_dump backup must succeed with the configured MinIO"

    s3 = s3_module.get_s3()
    dated = s3.head_object(Bucket=s3_module.S3_BUCKET, Key=key)
    assert dated["ContentLength"] > 0
    latest = s3.head_object(Bucket=s3_module.S3_BUCKET, Key=backup_module.DB_LATEST_KEY)
    assert latest["ContentLength"] == dated["ContentLength"]

    # Дамп в custom-формате pg_dump начинается с магической сигнатуры PGDMP.
    body = s3.get_object(Bucket=s3_module.S3_BUCKET, Key=key)["Body"].read(5)
    assert body == b"PGDMP"


def test_restore_database_from_latest_backup(sysdb):
    """Полный цикл: бэкап → потеря данных → restore_database() возвращает их.
    Восстановление идёт из latest.dump, снятого этим же тестом секунду назад,
    поэтому состояние БД для остальных тестов не меняется."""
    marker = f"RESTORE-MARKER-{uuid.uuid4()}"

    def _marker_count():
        with sysdb.pool.connection() as c:
            return c.execute("SELECT COUNT(*) FROM public.notification WHERE text = %s",
                             (marker,)).fetchone()[0]

    with sysdb.pool.connection() as c:
        c.execute("INSERT INTO public.notification (text, created_at, employee_id, is_read) "
                  "VALUES (%s, now(), 1, false)", (marker,))
    assert backup_module.backup_database(), "backup must succeed before restore test"

    with sysdb.pool.connection() as c:
        c.execute("DELETE FROM public.notification WHERE text = %s", (marker,))
    assert _marker_count() == 0          # «потеряли» данные

    assert backup_module.restore_database() is True
    assert _marker_count() == 1          # данные вернулись из бэкапа


def test_directory_snapshot_roundtrip():
    # Изолируем тест от рабочего снимка приложения — свой ключ на время теста.
    original_key = backup_module.DIRECTORY_STATE_KEY
    backup_module.DIRECTORY_STATE_KEY = f"backups/state/test-{uuid.uuid4()}.json"
    try:
        source = {
            # заведён в систему
            "onboarded": {"adUserId": "x1", "fullName": "Тест Заведённый",
                          "inSystem": True, "employee_id": 42, "role": "executor"},
            # не заведён (кандидат из AD)
            "candidate": {"adUserId": "x2", "fullName": "Тест Кандидат"},
        }
        assert backup_module.save_directory_snapshot(source) is True

        # Снимок не должен содержать паролей/identity — только onboarding-поля.
        raw = s3_module.get_s3().get_object(
            Bucket=s3_module.S3_BUCKET, Key=backup_module.DIRECTORY_STATE_KEY)["Body"].read()
        stored = json.loads(raw)
        assert stored["onboarded"] == {"inSystem": True, "employee_id": 42, "role": "executor"}
        assert stored["candidate"] == {}

        # Загрузка в «свежий» каталог (как из config.json): onboarded восстановлен,
        # у candidate onboarding-поля сняты, identity не тронута, чужой логин пропущен.
        target = {
            "onboarded": {"adUserId": "x1", "fullName": "Тест Заведённый"},
            "candidate": {"adUserId": "x2", "fullName": "Тест Кандидат",
                          "inSystem": True, "employee_id": 7, "role": "manager"},
        }
        applied = backup_module.load_directory_snapshot(target)
        assert applied == 2
        assert target["onboarded"]["inSystem"] is True
        assert target["onboarded"]["employee_id"] == 42
        assert target["onboarded"]["role"] == "executor"
        assert target["onboarded"]["fullName"] == "Тест Заведённый"
        assert "employee_id" not in target["candidate"]
        assert "role" not in target["candidate"]
        assert not target["candidate"].get("inSystem")
    finally:
        try:
            s3_module.get_s3().delete_object(Bucket=s3_module.S3_BUCKET,
                                             Key=backup_module.DIRECTORY_STATE_KEY)
        except Exception:
            pass
        backup_module.DIRECTORY_STATE_KEY = original_key


def test_load_missing_snapshot_is_noop():
    original_key = backup_module.DIRECTORY_STATE_KEY
    backup_module.DIRECTORY_STATE_KEY = f"backups/state/missing-{uuid.uuid4()}.json"
    try:
        directory = {"login": {"adUserId": "1", "inSystem": True, "employee_id": 1}}
        assert backup_module.load_directory_snapshot(directory) == 0
        assert directory["login"]["employee_id"] == 1   # каталог не тронут
    finally:
        backup_module.DIRECTORY_STATE_KEY = original_key
