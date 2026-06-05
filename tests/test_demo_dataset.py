from duna_orders.demo_customers import DEMO_TENANT_ID
from duna_orders.demo_dataset import generate_demo_dataset


def _json_records(items):
    return [item.model_dump(mode="json") for item in items]


def _stable_product_records(products):
    return [
        product.model_dump(
            mode="json",
            exclude={"created_at", "updated_at"},
        )
        for product in products
    ]


def test_generate_demo_dataset_returns_locked_row_counts() -> None:
    dataset = generate_demo_dataset()

    assert len(dataset.customers) == 730
    assert len(dataset.products) == 52
    assert len(dataset.orders) == 1500
    assert len(dataset.order_items) == 3889


def test_generate_demo_dataset_is_deterministic() -> None:
    first = generate_demo_dataset(seed=42)
    second = generate_demo_dataset(seed=42)

    assert first.tenant_id == second.tenant_id
    assert first.seed == second.seed
    assert _json_records(first.customers) == _json_records(second.customers)
    assert _stable_product_records(first.products) == _stable_product_records(
        second.products
    )
    assert _json_records(first.orders) == _json_records(second.orders)
    assert _json_records(first.order_items) == _json_records(second.order_items)


def test_generate_demo_dataset_threads_tenant_id() -> None:
    dataset = generate_demo_dataset()

    assert dataset.tenant_id == DEMO_TENANT_ID
    assert all(customer.tenant_id == DEMO_TENANT_ID for customer in dataset.customers)
    assert all(product.tenant_id == DEMO_TENANT_ID for product in dataset.products)
    assert all(order.tenant_id == DEMO_TENANT_ID for order in dataset.orders)
    assert all(item.tenant_id == DEMO_TENANT_ID for item in dataset.order_items)