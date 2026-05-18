# Architectural Decisions

## M1.1 — Data contract layer

Decision:
Define stable domain models, ID conventions, and spreadsheet schema before writing services or Google Sheets logic.

Why:
- The app needs one internal data shape independent of Streamlit, Google Sheets, and LLM providers.
- Stable IDs avoid coupling the system to spreadsheet row numbers.
- Snapshot fields on orders and order items preserve historical accuracy even if catalog/customer data changes later.
- Append-only stock movements make inventory auditable instead of only storing the final stock number.

Trade-off:
This adds a little structure upfront, but prevents the MVP from becoming a fragile Streamlit script.

## M1.2 — Storage contract and in-memory backend

Decision:
Define a StorageInterface and implement InMemoryStorage before connecting Google Sheets.

Why:
- Services should depend on a storage contract, not directly on Google Sheets.
- InMemoryStorage lets us build and test order/inventory logic without credentials, internet, or spreadsheet side effects.
- Returning deep copies prevents hidden mutation bugs while developing services.

Trade-off:
This adds a small abstraction early, but keeps the MVP testable and makes the future Sheets/Postgres migration cleaner.