from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from duna_orders.config import Settings
from scripts import smoke_preflight


ALEMBIC_HEAD_REVISION = "c5d8e9f0a1b2"


def make_settings(database_url: str, **overrides: object) -> Settings:
    values = {
        "duna_storage_backend": "postgres",
        "database_url": database_url,
        "twilio_auth_token": "super-secret-token",
        "twilio_webhook_public_url": "https://example.trycloudflare.com/webhooks/twilio/whatsapp",
        "webhook_tenant_id": "el-fogon-colombiano",
        "duna_outbound_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def make_outbound_settings(**overrides: object) -> Settings:
    values = {
        "duna_storage_backend": "postgres",
        "database_url": "sqlite:///unused.db",
        "twilio_auth_token": "super-secret-token",
        "twilio_webhook_public_url": "https://example.trycloudflare.com/webhooks/twilio/whatsapp",
        "webhook_tenant_id": "el-fogon-colombiano",
        "duna_outbound_enabled": True,
        "duna_outbound_tenant_id": "el-fogon-colombiano",
        "twilio_account_sid": "AC_TEST",
        "twilio_whatsapp_from": "whatsapp:+15551234567",
    }
    values.update(overrides)
    return Settings(**values)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'smoke.db'}"


def stamp_revision(database_url: str, revision: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": revision},
            )
    finally:
        engine.dispose()


def test_preflight_passes_with_configured_sqlite_database_at_head(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = sqlite_url(tmp_path)
    stamp_revision(database_url, ALEMBIC_HEAD_REVISION)

    exit_code = smoke_preflight.run_preflight(make_settings(database_url))

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "PASS: database connectivity - connected" in output
    assert (
        f"PASS: alembic revision state - current={ALEMBIC_HEAD_REVISION}; "
        f"head={ALEMBIC_HEAD_REVISION}"
    ) in output
    assert "SUMMARY: PASS" in output
    assert "super-secret-token" not in output


def test_preflight_fails_and_prints_upgrade_command_when_database_is_behind(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = sqlite_url(tmp_path)
    stamp_revision(database_url, "b7f4c8e2a901")

    exit_code = smoke_preflight.run_preflight(make_settings(database_url))

    output = capsys.readouterr().out

    assert exit_code == 1
    assert (
        "FAIL: alembic revision state - current=b7f4c8e2a901; "
        f"head={ALEMBIC_HEAD_REVISION}"
    ) in output
    assert "alembic upgrade head" in output
    assert "SUMMARY: FAIL" in output


def test_preflight_does_not_call_alembic_upgrade_when_database_is_behind(
    tmp_path: Path,
    capsys,
) -> None:
    database_url = sqlite_url(tmp_path)
    stamp_revision(database_url, "b7f4c8e2a901")

    with patch("alembic.command.upgrade") as upgrade:
        exit_code = smoke_preflight.run_preflight(make_settings(database_url))

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "alembic upgrade head" in output
    upgrade.assert_not_called()


def test_preflight_fails_gracefully_for_malformed_database_url(capsys) -> None:
    raw_database_url = "not-a-url"

    exit_code = smoke_preflight.run_preflight(make_settings(raw_database_url))

    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert exit_code == 1
    assert "FAIL: database URL - malformed or unsupported" in output
    assert "FAIL: alembic revision state - skipped because database engine was not created" in output
    assert "SUMMARY: FAIL" in output
    assert "Traceback" not in output
    assert raw_database_url not in output


def test_preflight_masks_database_password_on_connection_failure(
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "super-secret-db-password"
    database_url = f"postgresql+psycopg://duna:{password}@db.example.test:5432/orders"

    class FailingEngine:
        disposed = False

        def connect(self) -> object:
            raise SQLAlchemyError(f"could not connect to {database_url}")

        def dispose(self) -> None:
            self.disposed = True

    engine = FailingEngine()
    monkeypatch.setattr(smoke_preflight, "make_engine", lambda _: engine)

    exit_code = smoke_preflight.run_preflight(make_settings(database_url))

    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert exit_code == 1
    assert password not in captured.out
    assert password not in captured.err
    assert "FAIL" in output
    assert "SUMMARY: FAIL" in output
    assert "Traceback" not in output
    assert engine.disposed is True


def test_preflight_fails_gracefully_when_alembic_has_multiple_heads(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = sqlite_url(tmp_path)

    class MultiHeadScript:
        def get_heads(self) -> list[str]:
            return ["revision-a", "revision-b"]

    class FakeScriptDirectory:
        @staticmethod
        def from_config(_config: object) -> MultiHeadScript:
            return MultiHeadScript()

    monkeypatch.setattr(smoke_preflight, "ScriptDirectory", FakeScriptDirectory)

    exit_code = smoke_preflight.run_preflight(make_settings(database_url))

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "multiple alembic heads detected; resolve before smoke" in output
    assert "SUMMARY: FAIL" in output
    assert "Traceback" not in output


def test_preflight_fails_for_missing_required_settings(capsys) -> None:
    settings = make_settings(
        "",
        duna_storage_backend="memory",
        twilio_auth_token="",
        twilio_webhook_public_url="http://not-secure.test/webhooks/twilio/whatsapp",
        webhook_tenant_id="",
    )

    exit_code = smoke_preflight.run_preflight(settings)

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "FAIL: DUNA_STORAGE_BACKEND=postgres - expected postgres" in output
    assert "FAIL: DATABASE_URL present - missing or empty" in output
    assert "FAIL: twilio_auth_token present - missing or empty" in output
    assert "FAIL: twilio_webhook_public_url is https - invalid" in output
    assert "FAIL: webhook_tenant_id present - missing or empty" in output
    assert "SUMMARY: FAIL" in output


@pytest.mark.parametrize(
    ("public_url", "expected_detail"),
    [
        (
            "https://example.trycloudflare.com/webhooks/twilio/whatsapp/",
            "expected /webhooks/twilio/whatsapp",
        ),
        (
            "https://example.trycloudflare.com/webhooks/twilio/sms",
            "expected /webhooks/twilio/whatsapp",
        ),
    ],
)
def test_preflight_fails_for_wrong_twilio_webhook_path(
    capsys,
    public_url: str,
    expected_detail: str,
) -> None:
    settings = make_settings(
        "",
        twilio_webhook_public_url=public_url,
    )

    exit_code = smoke_preflight.run_preflight(settings)

    output = capsys.readouterr().out

    assert exit_code == 1
    assert f"FAIL: twilio_webhook_public_url path - {expected_detail}" in output
    assert "SUMMARY: FAIL" in output


def test_outbound_preflight_is_disabled_by_default() -> None:
    settings = make_settings("sqlite:///unused.db")

    checks = smoke_preflight.validate_outbound_settings(settings)

    assert checks == [
        smoke_preflight.CheckResult(
            name="DUNA_OUTBOUND_ENABLED",
            passed=True,
            detail="disabled",
        )
    ]


def test_outbound_preflight_fails_when_enabled_without_from_number() -> None:
    checks = smoke_preflight.validate_outbound_settings(
        make_outbound_settings(twilio_whatsapp_from="")
    )

    failed = {check.name: check.detail for check in checks if not check.passed}

    assert failed["TWILIO_WHATSAPP_FROM present"] == "missing or empty"


def test_outbound_preflight_fails_when_enabled_without_tenant_binding() -> None:
    checks = smoke_preflight.validate_outbound_settings(
        make_outbound_settings(duna_outbound_tenant_id="")
    )

    failed = {check.name: check.detail for check in checks if not check.passed}

    assert failed["DUNA_OUTBOUND_TENANT_ID present"] == "missing or empty"


@pytest.mark.parametrize(
    "overrides",
    [
        {"twilio_account_sid": ""},
        {"twilio_auth_token": ""},
    ],
)
def test_outbound_preflight_fails_when_enabled_without_account_credentials(
    overrides: dict[str, str],
) -> None:
    checks = smoke_preflight.validate_outbound_settings(make_outbound_settings(**overrides))

    assert any(not check.passed for check in checks)


def test_outbound_preflight_passes_with_valid_enabled_config() -> None:
    checks = smoke_preflight.validate_outbound_settings(make_outbound_settings())

    assert all(check.passed for check in checks)
