# Demo Data

## Purpose

The demo data exists to make the Duna Orders dashboard useful for internal validation, demos, and product conversations without touching runtime customer data.

It represents a fictional Colombian restaurant tenant:

- Business: El Fogón Colombiano
- Tenant ID: `el-fogon-colombiano`
- Target spreadsheet: configured through `GOOGLE_SHEETS_DEMO_SPREADSHEET_ID`

## Final demo dataset

Current M7.6 demo dataset:

- Customers: 730
- Products: 52
- Orders: 1,500
- Order items: 3,889

The dataset is deterministic and generated from seed `42`.

## Generation parameters

The demo data is designed to produce dashboard-realistic behavior:

- 30 regular customers
- 100 medium-tail customers
- 600 one-time customers
- 1,500 deterministic orders
- Demand-weighted daily order volume instead of flat date cycling
- Curated item weighting for Colombian restaurant signatures
- Curated pairings for common orders
- Some noisy/random behavior preserved to avoid an artificial dataset

Observed M7.6 realism outcomes:

- 729 distinct referenced customers
- 600 one-time customers
- 59.8% low-frequency order share
- Daily order range: 20–78 orders
- Strong item-pair signal, with top pair count around 142

## Regenerate commands

Run from the project root:

```powershell
cd C:\Duna\duna-orders
.\.venv\Scripts\Activate.ps1