from __future__ import annotations

from dataclasses import dataclass

from duna_orders.config import settings

import streamlit as st
from duna_orders.integrations.twilio_outbound import TwilioOutboundMessageAdapter
from duna_orders.storage.factory import build_storage
from duna_orders.demo_catalog import DemoCatalogFile, load_demo_catalog
from duna_orders.demo_messages import DemoMessagesFile, load_demo_messages
from duna_orders.parsing.base import ParserInterface
from duna_orders.services.outbound_acknowledgement import (
    OutboundAcknowledgementService,
)
from duna_orders.services.orders import OrderService
from duna_orders.services.inbound_draft_review import InboundDraftReviewService
from duna_orders.services.parsing import ParsingService
from duna_orders.services.tenant_scoped_reads import TenantScopedReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.memory import InMemoryStorage
from duna_orders.storage.sheets import GoogleSheetsStorage
from duna_orders.storage.outbound_messages import (
    OutboundAcknowledgementStore,
    PostgresOutboundAcknowledgementStore,
)
from duna_orders.storage.order_lifecycle import PostgresOrderLifecycleStore
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.processed_messages import PostgresProcessedMessageStore


@dataclass(frozen=True)
class OutboundAcknowledgementServiceSetup:
    service: OutboundAcknowledgementService | None
    acknowledgement_store: OutboundAcknowledgementStore | None = None
    tenant_id: str | None = None
    from_number: str | None = None
    unavailable_reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.service is not None


def get_storage() -> StorageInterface:
    return build_storage(settings)

def get_order_service(storage: StorageInterface) -> OrderService:
    if isinstance(storage, PostgresStorage):
        return OrderService(
            storage,
            lifecycle_store=PostgresOrderLifecycleStore(storage._session_factory),
        )

    return OrderService(storage)


def get_inbound_draft_review_service(
    storage: StorageInterface,
) -> InboundDraftReviewService | None:
    if not isinstance(storage, PostgresStorage):
        return None

    return InboundDraftReviewService(
        storage=storage,
        processed_message_store=PostgresProcessedMessageStore(storage._session_factory),
    )


def get_outbound_acknowledgement_service(
    storage: StorageInterface,
) -> OutboundAcknowledgementServiceSetup:
    if not settings.duna_outbound_enabled:
        return _outbound_unavailable("Outbound acknowledgement is disabled.")

    if not isinstance(storage, PostgresStorage):
        return _outbound_unavailable(
            "Outbound acknowledgement requires Postgres storage."
        )

    tenant_id = _optional_text(settings.duna_outbound_tenant_id)
    if tenant_id is None:
        return _outbound_unavailable(
            "Outbound acknowledgement tenant binding is not configured."
        )

    account_sid = _optional_text(settings.twilio_account_sid)
    if account_sid is None:
        return _outbound_unavailable("Twilio account SID is not configured.")

    auth_token = _optional_text(settings.twilio_auth_token)
    if auth_token is None:
        return _outbound_unavailable("Twilio auth token is not configured.")

    from_number = _optional_text(settings.twilio_whatsapp_from)
    if from_number is None:
        return _outbound_unavailable("Twilio WhatsApp sender is not configured.")

    acknowledgement_store = PostgresOutboundAcknowledgementStore(storage._session_factory)

    return OutboundAcknowledgementServiceSetup(
        service=OutboundAcknowledgementService(
            order_reader=TenantScopedReadService(storage),
            store=acknowledgement_store,
            adapter=TwilioOutboundMessageAdapter(
                account_sid=account_sid,
                auth_token=auth_token,
            ),
        ),
        acknowledgement_store=acknowledgement_store,
        tenant_id=tenant_id,
        from_number=from_number,
    )


def _build_anthropic_parser() -> ParserInterface:
    from duna_orders.parsing.anthropic_parser import AnthropicParser

    return AnthropicParser()


def get_parsing_service(storage: StorageInterface) -> ParsingService | None:
    if not settings.anthropic_api_key:
        return None

    return ParsingService(
        parser=_build_anthropic_parser(),
        storage=storage,
    )


@st.cache_data
def get_demo_catalog() -> DemoCatalogFile:
    return load_demo_catalog()


@st.cache_data
def get_demo_messages() -> DemoMessagesFile:
    return load_demo_messages()

def prepare_storage_catalog(
    storage: StorageInterface,
    catalog: DemoCatalogFile,
) -> bool:
    if isinstance(storage, InMemoryStorage):
        seed_inmemory_from_catalog(storage, catalog)
        return True

    if isinstance(storage, GoogleSheetsStorage):
        return bool(storage.unscoped_list_products(active_only=False))

    return True

def seed_inmemory_from_catalog(
    storage: InMemoryStorage,
    catalog: DemoCatalogFile,
) -> None:
    for product in catalog.products:
        storage.upsert_product(product)


def _outbound_unavailable(reason: str) -> OutboundAcknowledgementServiceSetup:
    return OutboundAcknowledgementServiceSetup(
        service=None,
        unavailable_reason=reason,
    )


def _optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None

    return value
