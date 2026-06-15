"""
Tests for email copies of notifications (src/email_module.py + the hook in
src/db_helpers.py). No SMTP server and no AD are needed: address resolution and
the send pipeline are exercised directly with the "console" transport, and the
actual send is captured by monkeypatching.

Covered:
  • mode "login_domain": address = directory login + organization domain;
  • mode "ad" under mock auth: address comes from the "email" field of MOCK_AD;
  • employees without a directory entry / without an email are skipped silently;
  • the enabled flag and ENV overrides (EMAIL_ENABLED, EMAIL_ORG_DOMAIN);
  • db_helpers.notify / create_notification fire the email hook;
  • errors in the email path never propagate to the caller.
"""

import threading
from datetime import datetime, timezone

import pytest

from src import db_helpers, email_module


@pytest.fixture
def email_cfg(monkeypatch):
    """Baseline test config: enabled, login_domain, console transport."""
    cfg = {
        "enabled": True,
        "mode": "login_domain",
        "organization_domain": "kptws.ru",
        "transport": "console",
    }
    monkeypatch.setitem(email_module.configData, "email", cfg)
    # Make sure container ENV does not leak into the tests.
    for var in ("EMAIL_ENABLED", "EMAIL_MODE", "EMAIL_ORG_DOMAIN", "EMAIL_TRANSPORT"):
        monkeypatch.delenv(var, raising=False)
    return cfg


# ─────────────────────────── address resolution ───────────────────────────

def test_login_domain_address(email_cfg):
    # employee_id 1 is orlova_m in MOCK_AD
    assert email_module.resolve_email(1) == "orlova_m@kptws.ru"


def test_login_domain_strips_leading_at(email_cfg):
    email_cfg["organization_domain"] = "@kptws.ru"
    assert email_module.resolve_email(2) == "ivanov_i@kptws.ru"


def test_login_domain_without_domain_returns_none(email_cfg):
    email_cfg["organization_domain"] = ""
    assert email_module.resolve_email(1) is None


def test_ad_mode_under_mock_auth_uses_directory_email(email_cfg):
    email_cfg["mode"] = "ad"
    # mock auth is the default; the "AD" mail is emulated by MOCK_AD["email"]
    assert email_module.resolve_email(1) == "maria.orlova@kptws.ru"


def test_ad_mode_entry_without_email_returns_none(email_cfg, monkeypatch):
    email_cfg["mode"] = "ad"
    directory = dict(email_module.configData["MOCK_AD"])
    directory["orlova_m"] = {k: v for k, v in directory["orlova_m"].items() if k != "email"}
    monkeypatch.setitem(email_module.configData, "MOCK_AD", directory)
    assert email_module.resolve_email(1) is None


def test_unknown_employee_returns_none(email_cfg):
    assert email_module.resolve_email(999999) is None


def test_env_overrides_win_over_config(email_cfg, monkeypatch):
    monkeypatch.setenv("EMAIL_ORG_DOMAIN", "other.example")
    assert email_module.resolve_email(1) == "orlova_m@other.example"
    # An empty ENV value means "not provided" (compose passes ${VAR:-}).
    monkeypatch.setenv("EMAIL_ORG_DOMAIN", "")
    assert email_module.resolve_email(1) == "orlova_m@kptws.ru"


# ─────────────────────────── sending pipeline ───────────────────────────

def test_console_transport_prints_the_letter(email_cfg, capsys):
    email_module.send_notification_email(1, "Вам назначена заявка: «Тест».", 42)
    out = capsys.readouterr().out
    assert "orlova_m@kptws.ru" in out
    assert "Вам назначена заявка" in out
    assert "42" in out


def test_disabled_sends_nothing(email_cfg, monkeypatch):
    email_cfg["enabled"] = False
    sent = []
    monkeypatch.setattr(email_module, "_send", lambda *a, **k: sent.append(a))
    email_module.send_notification_email(1, "text", 1)
    assert sent == []


def test_env_enabled_overrides_config(email_cfg, monkeypatch):
    email_cfg["enabled"] = False
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    sent = []
    monkeypatch.setattr(email_module, "_send",
                        lambda cfg, to, subject, body: sent.append(to))
    email_module.send_notification_email(1, "text", 1)
    assert sent == ["orlova_m@kptws.ru"]


def test_send_errors_never_propagate(email_cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(email_module, "_send", boom)
    email_module.send_notification_email(1, "text", 1)  # must not raise


def test_smtp_transport_sends_in_background_thread(email_cfg, monkeypatch):
    """transport=smtp must not block the caller: resolution+send go to a thread."""
    email_cfg["transport"] = "smtp"
    done = threading.Event()
    captured = []

    def fake_send(cfg, to, subject, body):
        captured.append((to, threading.current_thread() is not threading.main_thread()))
        done.set()

    monkeypatch.setattr(email_module, "_send", fake_send)
    email_module.send_notification_email(1, "bg test", 5)
    assert done.wait(5), "background send never happened"
    assert captured == [("orlova_m@kptws.ru", True)]


def test_ad_bind_failure_is_not_cached(email_cfg, monkeypatch):
    """A transient service-bind failure must not poison the per-login mail cache."""
    import ldap3

    monkeypatch.setitem(email_module.configData, "AD_auth",
                        {"server_adress": "dc.test.local", "Domain": "test.local",
                         "bind_user": "svc", "bind_password": "x"})
    email_module._ad_mail_cache.clear()

    state = {"bind_ok": False}

    class FakeMail:
        value = "maria.orlova@test.local"

    class FakeEntry:
        mail = FakeMail()

    class FakeConnection:
        def __init__(self, *a, **k):
            self.entries = [FakeEntry()]

        def bind(self):
            return state["bind_ok"]

        def search(self, *a, **k):
            return True

        def unbind(self):
            pass

    monkeypatch.setattr(ldap3, "Server", lambda *a, **k: None)
    monkeypatch.setattr(ldap3, "Connection", FakeConnection)

    # bind fails → no address AND no cache entry
    assert email_module._mail_from_ad("orlova_m") is None
    assert "orlova_m" not in email_module._ad_mail_cache

    # AD recovers → the very next attempt succeeds and is cached
    state["bind_ok"] = True
    assert email_module._mail_from_ad("orlova_m") == "maria.orlova@test.local"
    assert email_module._ad_mail_cache["orlova_m"] == "maria.orlova@test.local"
    email_module._ad_mail_cache.clear()


# ─────────────────────────── db_helpers hook ───────────────────────────

class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))


def test_notify_fires_email_hook(email_cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(email_module, "send_notification_email",
                        lambda emp, text, app_id=None: sent.append((emp, text, app_id)))
    cur = _FakeCursor()
    now = datetime.now(timezone.utc)
    db_helpers.notify(cur, "Заявка просрочена.", 3, 7, now)
    assert len(cur.executed) == 1          # the DB insert still happened
    assert sent == [(3, "Заявка просрочена.", 7)]


def test_notify_none_employee_no_insert_no_email(email_cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(email_module, "send_notification_email",
                        lambda *a, **k: sent.append(a))
    cur = _FakeCursor()
    db_helpers.notify(cur, "text", None, 7, datetime.now(timezone.utc))
    assert cur.executed == []
    assert sent == []


def test_notify_survives_email_failure(email_cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(email_module, "send_notification_email", boom)
    cur = _FakeCursor()
    db_helpers.notify(cur, "text", 3, 7, datetime.now(timezone.utc))  # must not raise
    assert len(cur.executed) == 1
