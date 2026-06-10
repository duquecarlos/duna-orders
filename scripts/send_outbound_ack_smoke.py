from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from duna_orders.config import Settings  # noqa: E402
from duna_orders.integrations.twilio_outbound import TwilioOutboundMessageAdapter  # noqa: E402
from duna_orders.services.outbound_acknowledgement import (  # noqa: E402
    OutboundAcknowledgementService,
    OutboundAcknowledgementOutcome,
)
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService  # noqa: E402
from duna_orders.storage.outbound_messages import (  # noqa: E402
    ORDER_CONFIRMED_ACK,
    PostgresOutboundAcknowledgementStore,
)
from duna_orders.storage.postgres import PostgresStorage  # noqa: E402
from duna_orders.storage.postgres_session import make_engine, make_session_factory  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one manual outbound acknowledgement smoke message.",
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--requested-by", required=True)
    parser.add_argument("--business-name")
    parser.add_argument("--retry-failed", action="store_true")
    return parser


def validate_smoke_settings(settings: Settings, *, tenant_id: str) -> None:
    if not settings.duna_outbound_enabled:
        raise ValueError("DUNA_OUTBOUND_ENABLED must be true for outbound smoke")
    if settings.duna_storage_backend != "postgres":
        raise ValueError("DUNA_STORAGE_BACKEND must be postgres for outbound smoke")
    if not settings.database_url or not settings.database_url.strip():
        raise ValueError("DATABASE_URL is required for outbound smoke")
    if not settings.duna_outbound_tenant_id or not settings.duna_outbound_tenant_id.strip():
        raise ValueError("DUNA_OUTBOUND_TENANT_ID is required for outbound smoke")
    if settings.duna_outbound_tenant_id != tenant_id:
        raise ValueError("DUNA_OUTBOUND_TENANT_ID must match --tenant-id")
    if not settings.twilio_account_sid or not settings.twilio_account_sid.strip():
        raise ValueError("TWILIO_ACCOUNT_SID is required for outbound smoke")
    if not settings.twilio_auth_token or not settings.twilio_auth_token.strip():
        raise ValueError("TWILIO_AUTH_TOKEN is required for outbound smoke")
    if not settings.twilio_whatsapp_from or not settings.twilio_whatsapp_from.strip():
        raise ValueError("TWILIO_WHATSAPP_FROM is required for outbound smoke")


def run_smoke(args: argparse.Namespace, settings: Settings | None = None) -> int:
    app_settings = settings or Settings()
    validate_smoke_settings(app_settings, tenant_id=args.tenant_id)

    assert app_settings.database_url is not None
    assert app_settings.twilio_account_sid is not None
    assert app_settings.twilio_auth_token is not None
    assert app_settings.twilio_whatsapp_from is not None

    engine = make_engine(app_settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        postgres_storage = PostgresStorage(session_factory)
        order_reader = TenantScopedReadService(postgres_storage)
        outbound_store = PostgresOutboundAcknowledgementStore(session_factory)
        adapter = TwilioOutboundMessageAdapter(
            account_sid=app_settings.twilio_account_sid,
            auth_token=app_settings.twilio_auth_token,
        )
        service = OutboundAcknowledgementService(
            order_reader=order_reader,
            store=outbound_store,
            adapter=adapter,
        )

        result = service.send_order_confirmed_acknowledgement(
            tenant_id=args.tenant_id,
            order_id=args.order_id,
            from_number=app_settings.twilio_whatsapp_from,
            requested_by=args.requested_by,
            business_name=args.business_name,
            retry_failed=args.retry_failed,
        )
        stored = outbound_store.get_for_order_acknowledgement(
            tenant_id=args.tenant_id,
            order_id=args.order_id,
            acknowledgement_type=ORDER_CONFIRMED_ACK,
        )

        print(f"outcome={result.outcome}")
        print(f"reason={result.reason}")
        print(f"attempted={result.attempted}")
        print(f"sent={result.sent}")
        if stored is not None:
            print(f"outbound_message_id={stored.outbound_message_id}")
            print(f"status={stored.status}")
            print(f"provider_message_id={stored.provider_message_id or ''}")

        if result.sent or result.outcome == OutboundAcknowledgementOutcome.SUPPRESSED_DUPLICATE:
            return 0

        return 1
    finally:
        engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run_smoke(args)
    except Exception as error:  # noqa: BLE001
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
