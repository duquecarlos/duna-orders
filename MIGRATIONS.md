# Migrations

## M4.2 — Restaurant demo fields

This migration updates the Google Sheets storage schema for the restaurant demo flow.

### products tab

Add column:

- `available_days`

`category` already exists in the current schema and does not need to be added again.

Recommended header order:

```text
product_id
product_name
aliases
category
available_days
unit
unit_price
active
current_stock
min_stock
notes
created_at
updated_at
```

### orders tab

Add columns:

- `fulfillment_type`
- `delivery_zone`
- `packaging_fee`
- `customer_notes`
- `payment_method`

Recommended header order:

```text
order_id
created_at
updated_at
customer_id
customer_name_snapshot
customer_phone_snapshot
raw_message
status
confirmed_at
subtotal
delivery_fee
packaging_fee
total
fulfillment_type
delivery_zone
customer_notes
payment_method
delivery_date
delivery_address
notes
confirmation_message
created_by
```

### order_items tab

Add column:

- `modifications`

Recommended header order:

```text
order_item_id
order_id
product_id
product_name_snapshot
unit_snapshot
quantity
unit_price_snapshot
line_total
modifications
validation_status
notes
```

### Notes

Run deterministic tests before applying this migration to any live spreadsheet.

After updating a spreadsheet header, run:

```powershell
pytest -m live_sheets -v
python scripts/smoke_google_sheets.py
```