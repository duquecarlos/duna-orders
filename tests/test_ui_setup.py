from __future__ import annotations

from duna_orders.domain.models import ParseResult, Product
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementService,
)
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_observation import (
    PostgresConversationObservationReads,
)
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.ui import setup
import pytest
from duna_orders.storage import factory as storage_factory
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.order_lifecycle import PostgresOrderLifecycleStore

class FakeParser(ParserInterface):
    @property
    def model_name(self) -> str:
        return "fake-parser"

    def parse(self, raw_message: str, products: list[Product]) -> ParseResult:
        raise NotImplementedError


class FakeTwilioOutboundMessageAdapter:
    calls: list[dict[str, str]] = []

    def __init__(self, *, account_sid: str, auth_token: str) -> None:
        self.calls.append(
            {
                "account_sid": account_sid,
                "auth_token": auth_token,
            }
        )


def _complete_outbound_settings(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_outbound_enabled", True)
    monkeypatch.setattr(setup.settings, "duna_outbound_tenant_id", "tenant-a")
    monkeypatch.setattr(setup.settings, "twilio_account_sid", "AC_secret")
    monkeypatch.setattr(setup.settings, "twilio_auth_token", "token-secret")
    monkeypatch.setattr(setup.settings, "twilio_whatsapp_from", "whatsapp:+15551234567")


def _postgres_storage() -> PostgresStorage:
    return PostgresStorage(lambda: None)


def test_get_storage_returns_inmemory_storage_by_default(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "memory")

    storage = setup.get_storage()

    assert isinstance(storage, InMemoryStorage)


def test_get_storage_returns_inmemory_storage_when_backend_is_blank(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", " ")

    storage = setup.get_storage()

    assert isinstance(storage, InMemoryStorage)


def test_get_storage_raises_for_invalid_backend(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sqlite")

    with pytest.raises(RuntimeError, match="DUNA_STORAGE_BACKEND must be"):
        setup.get_storage()


def test_get_storage_raises_for_sheets_backend_without_spreadsheet_id(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sheets")
    monkeypatch.setattr(setup.settings, "dashboard_target", "runtime")
    monkeypatch.setattr(setup.settings, "google_sheets_spreadsheet_id", None)

    with pytest.raises(RuntimeError, match="GOOGLE_SHEETS_SPREADSHEET_ID"):
        setup.get_storage()

def test_get_storage_builds_google_sheets_storage_when_configured(monkeypatch) -> None:
    calls = {}

    class FakeGoogleSheetsStorage:
        def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
            calls["spreadsheet_id"] = spreadsheet_id
            calls["credentials_path"] = credentials_path

    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sheets")
    monkeypatch.setattr(setup.settings, "dashboard_target", "runtime")
    monkeypatch.setattr(setup.settings, "google_sheets_spreadsheet_id", "sheet-123")
    monkeypatch.setattr(
        setup.settings,
        "google_sheets_credentials_path",
        "credentials/test-service-account.json",
    )
    monkeypatch.setattr(storage_factory, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    storage = setup.get_storage()

    assert isinstance(storage, FakeGoogleSheetsStorage)
    assert calls == {
        "spreadsheet_id": "sheet-123",
        "credentials_path": "credentials/test-service-account.json",
    }

def test_get_storage_builds_google_sheets_storage_for_demo_target(monkeypatch) -> None:
    calls = {}

    class FakeGoogleSheetsStorage:
        def __init__(self, *, spreadsheet_id: str, credentials_path: str) -> None:
            calls["spreadsheet_id"] = spreadsheet_id
            calls["credentials_path"] = credentials_path

    monkeypatch.setattr(setup.settings, "duna_storage_backend", "sheets")
    monkeypatch.setattr(setup.settings, "dashboard_target", "demo")
    monkeypatch.setattr(setup.settings, "google_sheets_spreadsheet_id", "runtime-sheet")
    monkeypatch.setattr(
        setup.settings,
        "google_sheets_demo_spreadsheet_id",
        "demo-sheet-456",
    )
    monkeypatch.setattr(
        setup.settings,
        "google_sheets_credentials_path",
        "credentials/test-service-account.json",
    )
    monkeypatch.setattr(storage_factory, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    storage = setup.get_storage()

    assert isinstance(storage, FakeGoogleSheetsStorage)
    assert calls == {
        "spreadsheet_id": "demo-sheet-456",
        "credentials_path": "credentials/test-service-account.json",
    }

def test_get_order_service_returns_service_bound_to_storage() -> None:
    storage = InMemoryStorage()

    service = setup.get_order_service(storage)

    assert isinstance(service, OrderService)
    assert service._storage is storage

def test_get_order_service_injects_lifecycle_store_for_postgres_storage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "postgres")
    monkeypatch.setattr(setup.settings, "database_url", "sqlite:///ui-lifecycle-test.db")

    storage = setup.get_storage()
    service = setup.get_order_service(storage)

    assert isinstance(storage, PostgresStorage)
    assert service._storage is storage
    assert isinstance(service._lifecycle_store, PostgresOrderLifecycleStore)


def test_get_conversation_observation_reads_returns_none_for_non_postgres_storage() -> None:
    reads = setup.get_conversation_observation_reads(InMemoryStorage())

    assert reads is None


def test_get_conversation_observation_reads_returns_reads_for_postgres_storage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "postgres")
    monkeypatch.setattr(
        setup.settings, "database_url", "sqlite:///ui-conversation-observation-test.db"
    )

    storage = setup.get_storage()
    reads = setup.get_conversation_observation_reads(storage)

    assert isinstance(storage, PostgresStorage)
    assert isinstance(reads, PostgresConversationObservationReads)
    assert reads._session_factory is storage._session_factory


def test_get_outbound_acknowledgement_service_returns_unavailable_when_disabled(
    monkeypatch,
) -> None:
    _complete_outbound_settings(monkeypatch)
    monkeypatch.setattr(setup.settings, "duna_outbound_enabled", False)
    FakeTwilioOutboundMessageAdapter.calls = []
    monkeypatch.setattr(
        setup,
        "TwilioOutboundMessageAdapter",
        FakeTwilioOutboundMessageAdapter,
    )

    result = setup.get_outbound_acknowledgement_service(_postgres_storage())

    assert result.is_available is False
    assert result.service is None
    assert result.unavailable_reason == "Outbound acknowledgement is disabled."
    assert FakeTwilioOutboundMessageAdapter.calls == []


def test_get_outbound_acknowledgement_service_blocks_non_postgres_storage(
    monkeypatch,
) -> None:
    _complete_outbound_settings(monkeypatch)

    result = setup.get_outbound_acknowledgement_service(InMemoryStorage())

    assert result.is_available is False
    assert result.unavailable_reason == "Outbound acknowledgement requires Postgres storage."


@pytest.mark.parametrize(
    ("setting_name", "expected_reason"),
    [
        (
            "duna_outbound_tenant_id",
            "Outbound acknowledgement tenant binding is not configured.",
        ),
        ("twilio_account_sid", "Twilio account SID is not configured."),
        ("twilio_auth_token", "Twilio auth token is not configured."),
        ("twilio_whatsapp_from", "Twilio WhatsApp sender is not configured."),
    ],
)
def test_get_outbound_acknowledgement_service_blocks_missing_config(
    monkeypatch,
    setting_name: str,
    expected_reason: str,
) -> None:
    _complete_outbound_settings(monkeypatch)
    monkeypatch.setattr(setup.settings, setting_name, " ")
    FakeTwilioOutboundMessageAdapter.calls = []
    monkeypatch.setattr(
        setup,
        "TwilioOutboundMessageAdapter",
        FakeTwilioOutboundMessageAdapter,
    )

    result = setup.get_outbound_acknowledgement_service(_postgres_storage())

    assert result.is_available is False
    assert result.service is None
    assert result.unavailable_reason == expected_reason
    assert FakeTwilioOutboundMessageAdapter.calls == []


def test_get_outbound_acknowledgement_service_unavailable_reason_hides_secrets(
    monkeypatch,
) -> None:
    _complete_outbound_settings(monkeypatch)
    monkeypatch.setattr(setup.settings, "twilio_whatsapp_from", None)

    result = setup.get_outbound_acknowledgement_service(_postgres_storage())

    assert result.unavailable_reason is not None
    assert "AC_secret" not in result.unavailable_reason
    assert "token-secret" not in result.unavailable_reason
    assert "whatsapp:+15551234567" not in result.unavailable_reason


def test_get_outbound_acknowledgement_service_constructs_when_ready(
    monkeypatch,
) -> None:
    _complete_outbound_settings(monkeypatch)
    FakeTwilioOutboundMessageAdapter.calls = []
    monkeypatch.setattr(
        setup,
        "TwilioOutboundMessageAdapter",
        FakeTwilioOutboundMessageAdapter,
    )

    result = setup.get_outbound_acknowledgement_service(_postgres_storage())

    assert result.is_available is True
    assert isinstance(result.service, OutboundAcknowledgementService)
    assert result.acknowledgement_store is not None
    assert result.tenant_id == "tenant-a"
    assert result.from_number == "whatsapp:+15551234567"
    assert result.unavailable_reason is None
    assert FakeTwilioOutboundMessageAdapter.calls == [
        {
            "account_sid": "AC_secret",
            "auth_token": "token-secret",
        }
    ]


def test_storage_interface_is_not_extended_for_outbound_ui_setup() -> None:
    assert all("outbound" not in name for name in StorageInterface.__abstractmethods__)


def test_get_parsing_service_returns_none_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "anthropic_api_key", None)

    service = setup.get_parsing_service(InMemoryStorage())

    assert service is None


def test_get_parsing_service_returns_service_when_api_key_is_set(monkeypatch) -> None:
    storage = InMemoryStorage()
    monkeypatch.setattr(setup.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(setup, "_build_anthropic_parser", lambda: FakeParser())

    service = setup.get_parsing_service(storage)

    assert isinstance(service, ParsingService)
    assert service._storage is storage

def test_get_demo_catalog_returns_validated_catalog() -> None:
    catalog = setup.get_demo_catalog()

    assert catalog.business.tenant_id == "el-fogon-colombiano"
    assert len(catalog.products) == 52

def test_get_demo_messages_returns_validated_messages() -> None:
    demo_messages = setup.get_demo_messages()

    assert len(demo_messages.messages) == 16
    assert demo_messages.messages[0].id.startswith("msg_")

def test_seed_inmemory_from_catalog_seeds_all_products() -> None:
    storage = InMemoryStorage()
    catalog = setup.get_demo_catalog()

    setup.seed_inmemory_from_catalog(storage, catalog)

    assert len(storage.unscoped_list_products(active_only=False)) == 52

def test_prepare_storage_catalog_seeds_inmemory_storage() -> None:
    storage = InMemoryStorage()
    catalog = setup.get_demo_catalog()

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is True
    assert len(storage.unscoped_list_products(active_only=False)) == 52


def test_prepare_storage_catalog_returns_true_for_seeded_sheets_storage(monkeypatch) -> None:
    catalog = setup.get_demo_catalog()

    class FakeGoogleSheetsStorage:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def unscoped_list_products(self, *, active_only: bool = True):
            return [catalog.products[0]]

        def upsert_product(self, product):
            self.upsert_calls += 1

    storage = FakeGoogleSheetsStorage()
    monkeypatch.setattr(setup, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is True
    assert storage.upsert_calls == 0

def test_get_storage_builds_postgres_storage_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(setup.settings, "duna_storage_backend", "postgres")
    monkeypatch.setattr(setup.settings, "database_url", "sqlite:///ui-postgres-test.db")

    storage = setup.get_storage()

    assert isinstance(storage, PostgresStorage)
def test_prepare_storage_catalog_returns_false_for_unseeded_sheets_storage(monkeypatch) -> None:
    catalog = setup.get_demo_catalog()

    class FakeGoogleSheetsStorage:
        def __init__(self) -> None:
            self.upsert_calls = 0

        def unscoped_list_products(self, *, active_only: bool = True):
            return []

        def upsert_product(self, product):
            self.upsert_calls += 1

    storage = FakeGoogleSheetsStorage()
    monkeypatch.setattr(setup, "GoogleSheetsStorage", FakeGoogleSheetsStorage)

    ready = setup.prepare_storage_catalog(storage, catalog)

    assert ready is False
    assert storage.upsert_calls == 0
