from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from duna_orders.domain.models import Order
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.processed_messages import ProcessedMessage


@dataclass(frozen=True)
class InboundDraftReviewItem:
    order: Order
    message_sid: str
    raw_inbound_body: str
    from_number: str | None


class ProcessedMessageReviewStore(Protocol):
    def list_messages_with_resulting_order(
        self,
        *,
        tenant_id: str,
    ) -> list[ProcessedMessage]:
        ...


class InboundDraftReviewService:
    def __init__(
        self,
        *,
        storage: StorageInterface,
        processed_message_store: ProcessedMessageReviewStore,
    ) -> None:
        self._storage = storage
        self._processed_message_store = processed_message_store

    def list_reviewable_inbound_drafts(
        self,
        *,
        tenant_id: str,
    ) -> list[InboundDraftReviewItem]:
        return self._list_inbound_orders_by_status(
            tenant_id=tenant_id,
            status="draft",
        )

    def list_confirmable_approved_orders(
        self,
        *,
        tenant_id: str,
    ) -> list[InboundDraftReviewItem]:
        return self._list_inbound_orders_by_status(
            tenant_id=tenant_id,
            status="approved",
        )

    def _list_inbound_orders_by_status(
        self,
        *,
        tenant_id: str,
        status: str,
    ) -> list[InboundDraftReviewItem]:
        review_items: list[InboundDraftReviewItem] = []
        messages = self._processed_message_store.list_messages_with_resulting_order(
            tenant_id=tenant_id,
        )

        for message in messages:
            if message.resulting_order_id is None:
                continue

            order = self._storage.get_order(message.resulting_order_id)

            if order is None:
                continue

            if order.tenant_id != tenant_id or order.status != status:
                continue

            review_items.append(
                InboundDraftReviewItem(
                    order=order,
                    message_sid=message.message_sid,
                    raw_inbound_body=message.raw_body or "",
                    from_number=message.from_number,
                )
            )

        return review_items
