from __future__ import annotations

from argparse import Namespace

import pytest

from duna_orders.config import Settings
from scripts import send_outbound_ack_smoke


def _settings(**overrides: object) -> Settings:
    values = {
        "duna_storage_backend": "postgres",
        "database_url": "sqlite:///smoke.db",
        "duna_outbound_enabled": True,
        "duna_outbound_tenant_id": "tenant-a",
        "twilio_account_sid": "AC_TEST",
        "twilio_auth_token": "auth-token",
        "twilio_whatsapp_from": "whatsapp:+15551234567",
    }
    values.update(overrides)
    return Settings(**values)


def test_manual_outbound_smoke_requires_enabled_flag() -> None:
    with pytest.raises(ValueError, match="DUNA_OUTBOUND_ENABLED"):
        send_outbound_ack_smoke.validate_smoke_settings(
            _settings(duna_outbound_enabled=False),
            tenant_id="tenant-a",
        )


def test_manual_outbound_smoke_requires_tenant_binding_match() -> None:
    with pytest.raises(ValueError, match="must match --tenant-id"):
        send_outbound_ack_smoke.validate_smoke_settings(
            _settings(duna_outbound_tenant_id="tenant-b"),
            tenant_id="tenant-a",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": ""},
        {"twilio_account_sid": ""},
        {"twilio_auth_token": ""},
        {"twilio_whatsapp_from": ""},
    ],
)
def test_manual_outbound_smoke_requires_send_configuration(overrides: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        send_outbound_ack_smoke.validate_smoke_settings(
            _settings(**overrides),
            tenant_id="tenant-a",
        )


def test_manual_outbound_smoke_parser_requires_explicit_order_context() -> None:
    parser = send_outbound_ack_smoke.build_parser()

    args = parser.parse_args(
        [
            "--tenant-id",
            "tenant-a",
            "--order-id",
            "ord_1",
            "--requested-by",
            "operator",
        ]
    )

    assert isinstance(args, Namespace)
    assert args.tenant_id == "tenant-a"
    assert args.order_id == "ord_1"
    assert args.requested_by == "operator"
