# Migrations
## M4.2.5b-D — Tenant ID columns for Google Sheets

This migration prepares Google Sheets storage for tenant-scoped data.

### Schema change

Add `tenant_id` as the second column in all six Google Sheets tabs, immediately after the primary ID column.

Required header order:

#### products

- product_id
- tenant_id
- product_name
- aliases
- category
- available_days
- unit
- unit_price
- active
- current_stock
- min_stock
- notes
- created_at
- updated_at

#### customers

- customer_id
- tenant_id
- customer_name
- customer_phone
- default_address
- notes
- created_at
- updated_at
- last_order_at

#### orders

- order_id
- tenant_id
- created_at
- updated_at
- customer_id
- customer_name_snapshot
- customer_phone_snapshot
- raw_message
- status
- confirmed_at
- subtotal
- delivery_fee
- packaging_fee
- total
- fulfillment_type
- delivery_zone
- customer_notes
- payment_method
- delivery_date
- delivery_address
- notes
- confirmation_message
- created_by

#### order_items

- order_item_id
- tenant_id
- order_id
- product_id
- product_name_snapshot
- unit_snapshot
- quantity
- unit_price_snapshot
- line_total
- modifications
- validation_status
- notes

#### stock_movements

- stock_movement_id
- tenant_id
- created_at
- product_id
- quantity_delta
- reason
- reference_id
- notes
- created_by

#### parse_log

- parse_id
- tenant_id
- created_at
- raw_message
- parsed_json
- model
- prompt_version
- latency_ms
- success
- error

### Data backfill

Use this tenant value for the current demo spreadsheet:

- el-fogon-colombiano

For the current test spreadsheet, the M4.2.5b-E migration path is:

1. Clean existing test rows.
2. Add `tenant_id` as the second column on all six tabs.
3. Keep only migrated rows with a valid `tenant_id`.

For a future production spreadsheet, the safe migration path is:

1. Add `tenant_id` as the second column on all six tabs.
2. Backfill existing rows with `el-fogon-colombiano` before deploying code that requires the new schema.
3. Deploy the code after the sheet headers and existing rows are aligned.

### Transition behavior

After M4.2.5b-D, `GoogleSheetsStorage` bootstrap validation intentionally raises `StorageConfigError` against any spreadsheet that does not include the new `tenant_id` columns.

This header validation drift is expected during the D/E transition and is resolved by the manual spreadsheet migration in M4.2.5b-E.

### Fresh bootstrap scenario

A brand-new spreadsheet with no existing tabs should be bootstrapped with all six tabs using the new `tenant_id` headers in position 2.

This is reviewed during D and can be verified live during E.

### Tooling

No automated migration tooling is provided for this step.

The spreadsheet edits are performed manually in M4.2.5b-E.

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