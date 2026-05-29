\# Duna Orders Architecture



Duna Orders is an order control system for WhatsApp-native businesses.



The product is not just a chatbot. The chatbot is one customer-facing intake channel. The core product is the operational system that turns informal messages into structured orders, reviewable drafts, inventory impact, traceability, and owner insights.



\## Product vision



Duna Orders is designed around three user roles.



\### 1. Customer



The customer interacts with the business through a conversational channel.



Current planned channel:



\- WhatsApp Business API

\- Twilio sandbox for early pilot exploration

\- Meta direct integration later if needed



Target behavior:



\- Customer sends informal order messages.

\- Bot understands product intent using the business catalog.

\- Bot asks follow-up questions when information is missing or ambiguous.

\- Bot creates a draft order.

\- Bot hands off the draft to the operator for review and confirmation.



The customer-facing bot is planned for Phase 5 onward. It is not part of the current Streamlit pilot.



\### 2. Operator



The operator reviews and controls daily order execution.



Current channel:



\- Local Streamlit UI



Current responsibilities:



\- Create or review draft orders.

\- Edit quantities, products, modifications, customer details, and fulfillment details.

\- Confirm orders.

\- Trigger stock movement through order confirmation.

\- Watch parser/bot behavior during pilot validation.



Future operator UI is still open. Options include:



\- Hosted web app

\- Local app

\- Mobile-friendly web interface

\- WhatsApp notification layer

\- WhatsApp-based lightweight actions



The current Streamlit UI remains acceptable through pilot validation.



\### 3. Owner



The owner needs business visibility, not necessarily raw operational detail.



Planned owner value:



\- Daily and weekly sales summaries.

\- Order volume.

\- Average order value.

\- Top products.

\- Low-stock alerts.

\- Product/category performance.

\- Delivery vs pickup split.

\- Payment method breakdown.

\- Customer retention and repeat-customer metrics.

\- Parser/bot performance insights.

\- Missed or ambiguous demand signals.



Owner dashboard work remains planned for Phase 4.



\## High-level data flow



```text

Customer

\-> WhatsApp / conversational channel

\-> Bot conversation

\-> Draft order

\-> Operator notification

\-> Operator review/edit

\-> Order confirmation

\-> Inventory impact

\-> Owner reports and insights

## Catalog metadata

Demo catalog files define business metadata once at the top level using a `business` block.

The `business` block contains:

```text
tenant_id
business_name
business_type
currency

## Tenant foundation status

M4.2.5b completed the tenant foundation for Duna Orders.

The system now uses `tenant_id` as the stable scope identifier for tenant-owned data. This keeps the architecture business-agnostic: Duna Orders can support restaurants, cafés, e-commerce sellers, small distributors, and other WhatsApp-native businesses without tying the core model to a `restaurant_id`.

Tenant-scoped persisted entities now include `tenant_id`:

- products
- customers
- orders
- order_items
- stock_movements
- parse_log

Google Sheets storage also uses `tenant_id` as column B / position 2 on every tenant-scoped tab.

The first demo tenant remains:

- tenant_id: `el-fogon-colombiano`
- business_name: `El Fogón Colombiano`
- business_type: `restaurant`
- currency: `COP`

The parser does not infer tenant identity from customer message text. The caller supplies tenant context explicitly.

## M8 - WhatsApp conversational ordering

M8 introduces WhatsApp conversational ordering and migrates runtime storage to Postgres to support transactional session state, queueing, idempotency, outbox semantics, and operator-gated order confirmation.

Detailed design: see ARCHITECTURE-M8.md.