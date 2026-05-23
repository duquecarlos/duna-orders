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

