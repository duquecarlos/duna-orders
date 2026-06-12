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
