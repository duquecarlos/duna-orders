from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from duna_orders.domain.phone import normalize_customer_phone
from duna_orders.storage.postgres_session import session_scope


DEFAULT_CLAIM_LEASE_DURATION = timedelta(seconds=60)


def normalize_customer_claim_key(tenant_id: str, customer_phone: str) -> str:
    """Derive the `customer_key` column value for a customer claim row.

    `tenant_id` is accepted (and validated) for a future migration seam where
    the key derivation may need it, but is intentionally not part of the
    returned value - `tenant_id` stays a separate claim-row column.
    """
    _require_text(tenant_id, "tenant_id")
    _require_text(customer_phone, "customer_phone")

    normalized = normalize_customer_phone(customer_phone)
    if normalized is None:
        raise ValueError("customer_phone is required")

    return normalized


class ConversationCustomerClaimStore(Protocol):
    def try_acquire(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        ...

    def release(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
    ) -> bool:
        ...

    def renew(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        ...


class PostgresConversationCustomerClaimStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def try_acquire(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")
        _require_text(holder_id, "holder_id")

        # Single atomic upsert: inserts a fresh row, or - only if the
        # existing row's lease has already expired - overwrites it with the
        # new holder. A live (non-expired) row matches no UPDATE branch, so
        # RETURNING yields nothing and this is a no-op. The DB clock (now())
        # is authoritative for both the expiry comparison and the new
        # acquired_at/lease_expires_at/updated_at values, so acquisition
        # correctness does not depend on app-server clocks agreeing.
        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    INSERT INTO conversation_customer_claims
                        (tenant_id, customer_key, holder_id, acquired_at, lease_expires_at, updated_at)
                    VALUES
                        (:tenant_id, :customer_key, :holder_id, now(), now() + :lease_duration, now())
                    ON CONFLICT (tenant_id, customer_key) DO UPDATE SET
                        holder_id = EXCLUDED.holder_id,
                        acquired_at = EXCLUDED.acquired_at,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        updated_at = EXCLUDED.updated_at
                    WHERE conversation_customer_claims.lease_expires_at <= now()
                    RETURNING holder_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "customer_key": customer_key,
                    "holder_id": holder_id,
                    "lease_duration": lease_duration,
                },
            ).first()

        return row is not None

    def release(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
    ) -> bool:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")
        _require_text(holder_id, "holder_id")

        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    DELETE FROM conversation_customer_claims
                    WHERE tenant_id = :tenant_id
                      AND customer_key = :customer_key
                      AND holder_id = :holder_id
                    RETURNING holder_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "customer_key": customer_key,
                    "holder_id": holder_id,
                },
            ).first()

        return row is not None

    def renew(
        self,
        *,
        tenant_id: str,
        customer_key: str,
        holder_id: str,
        lease_duration: timedelta = DEFAULT_CLAIM_LEASE_DURATION,
    ) -> bool:
        _require_text(tenant_id, "tenant_id")
        _require_text(customer_key, "customer_key")
        _require_text(holder_id, "holder_id")

        with session_scope(self._session_factory) as session:
            row = session.execute(
                text(
                    """
                    UPDATE conversation_customer_claims
                    SET lease_expires_at = now() + :lease_duration,
                        updated_at = now()
                    WHERE tenant_id = :tenant_id
                      AND customer_key = :customer_key
                      AND holder_id = :holder_id
                    RETURNING holder_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "customer_key": customer_key,
                    "holder_id": holder_id,
                    "lease_duration": lease_duration,
                },
            ).first()

        return row is not None


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
