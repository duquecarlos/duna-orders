from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

ENFORCED_RUNTIME_READ_MODULES = frozenset(
    {
        Path("pages/1_New_Order.py"),
        Path("pages/2_Orders_Today.py"),
        Path("pages/6_Conversations.py"),
        Path("src/duna_orders/services/outbound_acknowledgement.py"),
        Path("src/duna_orders/services/dashboard_read_scenario.py"),
        Path("src/duna_orders/services/conversation_advancement.py"),
        Path("src/duna_orders/web/inbound.py"),
        Path("src/duna_orders/web/app.py"),
    }
)

READ_ONLY_RUNTIME_PAGES = frozenset(
    {
        Path("pages/6_Conversations.py"),
    }
)

REQUIRED_OBSERVATION_DETAIL_READ_PAGES = frozenset(
    {
        Path("pages/6_Conversations.py"),
    }
)

FORBIDDEN_MUTATION_CALL_NAMES = frozenset(
    {
        "mark_draft_created",
        "record_advancement_attempt",
        "append_turn_if_new",
        "get_or_create_open_session",
        "create_draft",
        "confirm_order",
        "confirm_order_atomically",
        "review_inbound_draft",
        "confirm_approved_order",
        "transition_order_status",
        "list_turns",
    }
)

FORBIDDEN_MUTATION_IMPORT_NAMES = frozenset(
    {
        "OrderService",
        "ConversationAdvancementService",
        "InboundDraftReviewService",
        "ConversationStateStore",
        "PostgresConversationStateStore",
    }
)

KNOWN_STAGE1_RUNTIME_READ_MODULES = frozenset(
    {
        Path("pages/1_New_Order.py"),
        Path("pages/2_Orders_Today.py"),
        Path("src/duna_orders/services/outbound_acknowledgement.py"),
        Path("src/duna_orders/services/dashboard_read_scenario.py"),
        Path("src/duna_orders/web/inbound.py"),
    }
)

FORBIDDEN_BROAD_READS = frozenset(
    {
        "get_order",
        "list_customers",
        "list_orders",
        "list_products",
        "list_stock_movements",
        "unscoped_list_customers",
        "unscoped_list_products",
    }
)

BROAD_STORAGE_RECEIVERS = frozenset(
    {
        "storage",
        "self._storage",
        "st.session_state.storage",
    }
)

CLAIM_STORE_MODULE = "duna_orders.storage.conversation_customer_claims"

FORBIDDEN_CLAIM_STORE_IMPORT_NAMES = frozenset(
    {
        "PostgresConversationCustomerClaimStore",
        "ConversationCustomerClaimStore",
        "normalize_customer_claim_key",
    }
)

CLAIM_STORE_SCAN_ROOTS = frozenset(
    {
        Path("src/duna_orders/services"),
        Path("src/duna_orders/web"),
        Path("src/duna_orders/ui"),
        Path("pages"),
    }
)

CLAIM_STORE_ALLOWED_IMPORT_MODULES = frozenset(
    {
        Path("src/duna_orders/web/app.py"),
    }
)


def test_stage1_runtime_read_modules_are_all_guarded() -> None:
    assert KNOWN_STAGE1_RUNTIME_READ_MODULES <= ENFORCED_RUNTIME_READ_MODULES


def test_stage1_runtime_read_modules_do_not_call_broad_storage_reads() -> None:
    violations: list[str] = []

    for relative_path in sorted(ENFORCED_RUNTIME_READ_MODULES):
        module_path = REPO_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            if not isinstance(func, ast.Attribute):
                continue

            if func.attr not in FORBIDDEN_BROAD_READS:
                continue

            receiver = ast.unparse(func.value)
            if receiver in BROAD_STORAGE_RECEIVERS:
                violations.append(
                    f"{relative_path}:{node.lineno} calls {receiver}.{func.attr}(...)"
                )

    assert violations == []


def test_conversation_detail_pages_use_observation_detail_read_not_list_turns() -> None:
    for relative_path in sorted(REQUIRED_OBSERVATION_DETAIL_READ_PAGES):
        module_path = REPO_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        call_names = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        assert "get_conversation_observation_detail" in call_names, relative_path
        assert "list_turns" not in call_names, relative_path


def test_read_only_runtime_pages_do_not_use_mutation_apis() -> None:
    violations: list[str] = []

    for relative_path in sorted(READ_ONLY_RUNTIME_PAGES):
        module_path = REPO_ROOT / relative_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                else:
                    name = None

                if name in FORBIDDEN_MUTATION_CALL_NAMES:
                    violations.append(f"{relative_path}:{node.lineno} calls {name}(...)")

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MUTATION_IMPORT_NAMES:
                        violations.append(
                            f"{relative_path}:{node.lineno} imports {alias.name}"
                        )

    assert violations == []


def test_conversation_customer_claim_store_import_is_restricted_to_web_app() -> None:
    """M9.6D wires the customer-claim store only into the webhook entrypoint.

    src/duna_orders/web/app.py is the sole allowed importer; conversation
    advancement reaches the claim lease via the renew_customer_claim
    callback instead, so it - and every other module under the scanned
    roots - must stay free of this import.
    """
    violations: list[str] = []

    for root in sorted(CLAIM_STORE_SCAN_ROOTS):
        root_path = REPO_ROOT / root

        if not root_path.exists():
            continue

        for module_path in sorted(root_path.rglob("*.py")):
            relative_path = module_path.relative_to(REPO_ROOT)

            if relative_path in CLAIM_STORE_ALLOWED_IMPORT_MODULES:
                continue

            tree = ast.parse(module_path.read_text(encoding="utf-8"))

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module == CLAIM_STORE_MODULE:
                        violations.append(
                            f"{relative_path}:{node.lineno} imports from {CLAIM_STORE_MODULE}"
                        )

                    for alias in node.names:
                        if alias.name in FORBIDDEN_CLAIM_STORE_IMPORT_NAMES:
                            violations.append(
                                f"{relative_path}:{node.lineno} imports {alias.name}"
                            )

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == CLAIM_STORE_MODULE:
                            violations.append(
                                f"{relative_path}:{node.lineno} imports {CLAIM_STORE_MODULE}"
                            )

    assert violations == []


def test_web_app_imports_conversation_customer_claim_store() -> None:
    """The sole allowlisted importer must actually use the claim store.

    Keeps test_conversation_customer_claim_store_import_is_restricted_to_web_app
    meaningful - an unused allowlist entry would let the guard pass while
    silently no longer covering anything.
    """
    module_path = REPO_ROOT / "src/duna_orders/web/app.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == CLAIM_STORE_MODULE:
            imported_names.update(alias.name for alias in node.names)

    assert imported_names & FORBIDDEN_CLAIM_STORE_IMPORT_NAMES
