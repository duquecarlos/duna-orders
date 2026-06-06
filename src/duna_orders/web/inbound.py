import logging

from duna_orders.domain.models import DraftItemRequest, Order
from duna_orders.parsing.base import ParserInterface
from duna_orders.parsing.exceptions import ParserError
from duna_orders.services.exceptions import ServiceError
from duna_orders.services.orders import OrderService
from duna_orders.services.parsing import ParsingService
from duna_orders.storage.base import StorageInterface

logger = logging.getLogger(__name__)


def create_draft_from_inbound_message(
    *,
    storage: StorageInterface,
    parser: ParserInterface,
    tenant_id: str,
    sender: str | None,
    body: str,
) -> Order | None:
    cleaned_body = body.strip()

    if not cleaned_body:
        logger.info("Skipping empty inbound WhatsApp message.")
        return None

    products = [
        product
        for product in storage.list_products(active_only=True)
        if product.tenant_id == tenant_id
    ]

    try:
        parse_result = ParsingService(parser, storage).parse(
            tenant_id=tenant_id,
            raw_message=cleaned_body,
            products=products,
        )
        request = parse_result.request

        normalized_sender = _twilio_whatsapp_sender_to_phone(sender)
        request = request.model_copy(
            update={
                "tenant_id": tenant_id,
                "raw_message": cleaned_body,
                "customer_phone": normalized_sender or request.customer_phone,
                "items": [
                    DraftItemRequest(
                        tenant_id=tenant_id,
                        product_id=item.product_id,
                        quantity=item.quantity,
                        modifications=item.modifications,
                    )
                    for item in request.items
                ],
            },
            deep=True,
        )

        return OrderService(storage).create_draft(request)
    except (ParserError, ServiceError, ValueError) as error:
        logger.warning("Skipping inbound WhatsApp message: %s", error)
        return None


def _twilio_whatsapp_sender_to_phone(sender: str | None) -> str | None:
    if sender is None:
        return None

    cleaned = sender.strip()

    if not cleaned:
        return None

    if cleaned.lower().startswith("whatsapp:"):
        return cleaned.split(":", 1)[1].strip()

    return cleaned