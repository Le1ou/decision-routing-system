"""
email_module.py — дублирование внутрисистемных уведомлений на рабочую почту.

Каждое уведомление, создаваемое через db_helpers.notify / create_notification,
дополнительно отправляется письмом на рабочий адрес сотрудника. Модуль полностью
best-effort: любая ошибка (нет адреса, недоступен SMTP/AD) логируется и НЕ ломает
действие, породившее уведомление. Сидовые уведомления (seed.py / seed_demo.py)
вставляются в БД напрямую и писем не порождают — это историческая демо-лента.

Конфигурация — config.json → "email" (+ ENV-override, как везде в проекте):

    enabled              EMAIL_ENABLED        выключатель (по умолчанию false)
    mode                 EMAIL_MODE           источник адреса:
                                              "login_domain" — login + домен организации
                                                (orlova_m + kptws.ru → orlova_m@kptws.ru)
                                              "ad" — атрибут mail из Active Directory
    organization_domain  EMAIL_ORG_DOMAIN     доменная часть для login_domain
    transport            EMAIL_TRANSPORT      "console" (письмо в лог, dev/тесты)
                                              или "smtp" (реальная отправка)
    smtp.*               SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/
                         SMTP_STARTTLS/SMTP_SSL/EMAIL_FROM/SMTP_TIMEOUT

Режим "ad" в боевой схеме (AUTH_MODE=ad) ищет mail LDAP-поиском под сервисной
учёткой (AD_auth.bind_user/bind_password или ENV AD_BIND_USER/AD_BIND_PASSWORD) —
уведомления рассылаются фоновыми задачами, где пользовательских кредов нет;
результат кэшируется в памяти. В mock-схеме адрес берётся из поля "email"
записи MOCK_AD — так режим тестируется без домен-контроллера.

Логин сотрудника, как и везде, в БД не хранится: восстанавливается обратным
поиском по каталогу (MOCK_AD: inSystem + employee_id).
"""

import os
import smtplib
import threading
from email.message import EmailMessage

from src.application_module import (
    AD_CONNECT_TIMEOUT, _ad_config, _auth_mode, _domain_to_base, configData,
)

# Кэш login → mail для режима "ad" (LDAP-поиск делается один раз на процесс).
_ad_mail_cache: dict = {}
_ad_mail_cache_lock = threading.Lock()


def _env(name: str):
    """ENV-значение либо None; пустая строка = «не задано» (compose пробрасывает ${VAR:-})."""
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _env_bool(name: str, fallback: bool) -> bool:
    value = _env(name)
    if value is None:
        return fallback
    return value.lower() in ("1", "true", "yes", "on")


def email_config() -> dict:
    """Действующие настройки почты: ENV поверх config.json → "email"."""
    cfg = configData.get("email", {}) or {}
    smtp = cfg.get("smtp", {}) or {}

    domain = (_env("EMAIL_ORG_DOMAIN") or cfg.get("organization_domain") or "").strip().lstrip("@")
    try:
        port = int(_env("SMTP_PORT") or smtp.get("port") or 587)
    except (TypeError, ValueError):
        port = 587
    try:
        timeout = int(_env("SMTP_TIMEOUT") or smtp.get("timeout_seconds") or 10)
    except (TypeError, ValueError):
        timeout = 10

    return {
        "enabled": _env_bool("EMAIL_ENABLED", bool(cfg.get("enabled", False))),
        "mode": (_env("EMAIL_MODE") or cfg.get("mode") or "login_domain").lower(),
        "domain": domain,
        "transport": (_env("EMAIL_TRANSPORT") or cfg.get("transport") or "console").lower(),
        "smtp_host": _env("SMTP_HOST") or (smtp.get("host") or "").strip(),
        "smtp_port": port,
        "smtp_user": _env("SMTP_USER") or (smtp.get("username") or "").strip(),
        "smtp_password": _env("SMTP_PASSWORD") or (smtp.get("password") or ""),
        "smtp_starttls": _env_bool("SMTP_STARTTLS", bool(smtp.get("starttls", True))),
        "smtp_ssl": _env_bool("SMTP_SSL", bool(smtp.get("use_ssl", False))),
        "from": _env("EMAIL_FROM") or (smtp.get("from") or "").strip(),
        "timeout": timeout,
    }


def _login_for_employee(employee_id):
    """Обратный поиск по каталогу: employee_id → (login, запись каталога)."""
    for login, entry in (configData.get("MOCK_AD", {}) or {}).items():
        if entry.get("inSystem") and entry.get("employee_id") == int(employee_id):
            return login, entry
    return None, None


def _mail_from_ad(login: str) -> str | None:
    """Атрибут mail из Active Directory под сервисной учёткой, с кэшем."""
    with _ad_mail_cache_lock:
        if login in _ad_mail_cache:
            return _ad_mail_cache[login]

    cfg = _ad_config()
    ad_auth = configData.get("AD_auth", {}) or {}
    bind_user = _env("AD_BIND_USER") or (ad_auth.get("bind_user") or "").strip()
    bind_password = _env("AD_BIND_PASSWORD") or ad_auth.get("bind_password") or ""
    if not cfg["server"] or not cfg["domain"] or not bind_user:
        print("[email] AD mail lookup skipped: server/domain/bind_user not configured")
        return None
    if "@" not in bind_user:
        bind_user = f"{bind_user}@{cfg['domain']}"

    mail = None
    try:
        import ldap3
        server = ldap3.Server(cfg["server"], port=cfg["port"], use_ssl=cfg["use_ssl"],
                              get_info=ldap3.NONE, connect_timeout=AD_CONNECT_TIMEOUT)
        conn = ldap3.Connection(server, user=bind_user, password=bind_password,
                                authentication="SIMPLE", receive_timeout=AD_CONNECT_TIMEOUT)
        if not conn.bind():
            # Сбой bind (сервер недоступен / неверная сервисная учётка) — НЕ кэшируем,
            # следующая отправка повторит поиск.
            print(f"[email] AD service bind failed for {bind_user}")
            return None
        base = cfg["search_base"] or _domain_to_base(cfg["domain"])
        search_filter = (f"(|(userPrincipalName={login}@{cfg['domain']})"
                         f"(sAMAccountName={login}))")
        if conn.search(base, search_filter, attributes=["mail"]) and conn.entries:
            value = conn.entries[0].mail
            mail = str(value.value).strip() if value and value.value else None
        conn.unbind()
    except Exception as e:
        print(f"[email] AD mail lookup error for {login}: {e}")
        return None  # не кэшируем сбой — следующая попытка повторит поиск

    # Сюда доходим только после успешного поиска: кэшируется и найденный адрес,
    # и подтверждённое отсутствие mail у пользователя.
    with _ad_mail_cache_lock:
        _ad_mail_cache[login] = mail
    return mail


def resolve_email(employee_id, cfg: dict | None = None) -> str | None:
    """Рабочий адрес сотрудника согласно активному режиму, либо None."""
    cfg = cfg or email_config()
    login, entry = _login_for_employee(employee_id)
    if not login:
        return None
    if cfg["mode"] == "ad":
        if _auth_mode() == "ad":
            return _mail_from_ad(login)
        # mock-схема аутентификации: «AD» эмулируется полем email в MOCK_AD
        return (entry.get("email") or "").strip() or None
    # login_domain (по умолчанию)
    if not cfg["domain"]:
        print("[email] organization_domain is not configured — cannot build address")
        return None
    return f"{login}@{cfg['domain']}"


def _send(cfg: dict, to: str, subject: str, body: str) -> None:
    if cfg["transport"] == "console":
        print(f"[email] to={to} subject={subject!r} body={body!r}")
        return
    msg = EmailMessage()
    msg["From"] = cfg["from"] or (cfg["smtp_user"] or f"noreply@{cfg['domain'] or 'localhost'}")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if cfg["smtp_ssl"] else smtplib.SMTP
    with smtp_cls(cfg["smtp_host"], cfg["smtp_port"], timeout=cfg["timeout"]) as smtp:
        if cfg["smtp_starttls"] and not cfg["smtp_ssl"]:
            smtp.starttls()
        if cfg["smtp_user"]:
            smtp.login(cfg["smtp_user"], cfg["smtp_password"])
        smtp.send_message(msg)


def _resolve_and_send(cfg: dict, employee_id, subject: str, body: str) -> None:
    """Определить адрес и отправить. Никогда не бросает (работает и в фоновом потоке)."""
    try:
        to = resolve_email(employee_id, cfg)
        if not to:
            print(f"[email] skip: no address for employee {employee_id} (mode={cfg['mode']})")
            return
        _send(cfg, to, subject, body)
    except Exception as e:
        print(f"[email] send failed for employee {employee_id}: {e}")


def send_notification_email(employee_id, text, application_id=None) -> None:
    """Продублировать уведомление письмом. Никогда не бросает исключений.

    Вызывается из db_helpers.notify, т.е. зачастую при ОТКРЫТОЙ транзакции
    (тик событий/маршрутизации держит advisory-lock, чат — свою транзакцию).
    Поэтому при transport=smtp определение адреса (возможный LDAP-поиск) и сетевая
    отправка уходят в фоновый daemon-поток (fire-and-forget) — медленные почтовый
    сервер или AD не задерживают тик и запросы. Console-транспорт мгновенный и
    остаётся синхронным. Обратная сторона best-effort: письмо может уйти и для
    транзакции, которая затем откатится (уведомления в БД при этом не будет).
    """
    try:
        cfg = email_config()
        if not cfg["enabled"] or employee_id is None:
            return
        subject = "Уведомление системы маршрутизации заявок"
        body = str(text)
        if application_id is not None:
            body += f"\n\nЗаявка №{application_id}."
        if cfg["transport"] == "smtp":
            threading.Thread(target=_resolve_and_send, args=(cfg, employee_id, subject, body),
                             daemon=True).start()
        else:
            _resolve_and_send(cfg, employee_id, subject, body)
    except Exception as e:
        print(f"[email] send failed for employee {employee_id}: {e}")
