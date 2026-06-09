from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from duna_orders.domain.models import Order
from duna_orders.services.diagnostic_reads import DiagnosticReadService
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.processed_messages import ProcessedMessage


@dataclass(frozen=True)
class InboundDraftReviewItem:
    order: Order
    message_sid: str
    raw_inbound_body: str
    from_number: str | None


@dataclass(frozen=True)
class InboundReviewDiagnostics:
    missing_order_count: int = 0
    tenant_mismatch_count: int = 0
    confirmed_count: int = 0
    cancelled_count: int = 0
    other_status_count: int = 0
    draft_count: int = 0
    approved_count: int = 0

    @property
    def skipped_count(self) -> int:
        return (
            self.missing_order_count
            + self.tenant_mismatch_count
            + self.confirmed_count
            + self.cancelled_count
            + self.other_status_count
        )


@dataclass(frozen=True)
class InboundReviewSnapshot:
    draft_items: list[InboundDraftReviewItem]
    approved_items: list[InboundDraftReviewItem]
    diagnostics: InboundReviewDiagnostics


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
        self._processed_message_store = processed_message_store
        self._diagnostic_reads = DiagnosticReadService(storage)

    def list_reviewable_inbound_drafts(
        self,
        *,
        tenant_id: str,
    ) -> list[InboundDraftReviewItem]:
        return self.get_inbound_review_snapshot(tenant_id=tenant_id).draft_items

    def list_confirmable_approved_orders(
        self,
        *,
        tenant_id: str,
    ) -> list[InboundDraftReviewItem]:
        return self.get_inbound_review_snapshot(tenant_id=tenant_id).approved_items

    def get_inbound_review_snapshot(
        self,
        *,
        tenant_id: str,
    ) -> InboundReviewSnapshot:
        draft_items: list[InboundDraftReviewItem] = []
        approved_items: list[InboundDraftReviewItem] = []
        missing_order_count = 0
        tenant_mismatch_count = 0
        confirmed_count = 0
        cancelled_count = 0
        other_status_count = 0

        messages = self._processed_message_store.list_messages_with_resulting_order(
            tenant_id=tenant_id,
        )

        for message in messages:
            if message.resulting_order_id is None:
                continue

            order = self._diagnostic_reads.get_order_for_diagnostics(
                message.resulting_order_id
            )

            if order is None:
                missing_order_count += 1
                continue

            if order.tenant_id != tenant_id:
                tenant_mismatch_count += 1
                continue

            item = (
                InboundDraftReviewItem(
                    order=order,
                    message_sid=message.message_sid,
                    raw_inbound_body=message.raw_body or "",
                    from_number=message.from_number,
                )
            )

            if order.status == "draft":
                draft_items.append(item)
            elif order.status == "approved":
                approved_items.append(item)
            elif order.status == "confirmed":
                confirmed_count += 1
            elif order.status == "cancelled":
                cancelled_count += 1
            else:
                other_status_count += 1

        return InboundReviewSnapshot(
            draft_items=draft_items,
            approved_items=approved_items,
            diagnostics=InboundReviewDiagnostics(
                missing_order_count=missing_order_count,
                tenant_mismatch_count=tenant_mismatch_count,
                confirmed_count=confirmed_count,
                cancelled_count=cancelled_count,
                other_status_count=other_status_count,
                draft_count=len(draft_items),
                approved_count=len(approved_items),
            ),
        )
