# M8.5B Tenant-Scoped Reads Design

Status: proposed design only.

Base: `f183587 docs: close Postgres hardening`

This document defines the tenant-scoped read architecture direction for the
current M8 Postgres runtime hardening work. It does not propose code changes in
this pass.

## 1. Title and Status

M8.5B locks the design direction for tenant-scoped reads after the M8.5A
Postgres storage inspection.

Decision summary:

* Immediate direction: Option E stage 1, add a thin tenant-scoped read layer
  above `StorageInterface`.
* Destination: Option C, evolve `StorageInterface` read methods to require
  `tenant_id` across all backends after the scoped contract is proven.
* Stage 1 is taken toward C, not instead of C.

No implementation, migration, schema change, or `StorageInterface` change is
part of this document pass.

## 2. Context

M8.5A confirmed that `PostgresStorage` implements the current
`StorageInterface`, while Postgres-only capabilities remain outside that
interface:

* processed messages;
* order lifecycle store;
* atomic approved confirmation;
* demo and bulk helpers.

The remaining architecture issue is that the current `StorageInterface` is
mostly ID/global-list oriented, not tenant-scoped. Broad read methods include:

* `get_order(order_id)`;
* `list_orders(...)`;
* `list_products(...)`;
* `list_customers()`;
* `list_stock_movements(...)`.

Critical service paths currently add tenant checks. For example,
`OrderService.confirm_approved_order(...)`, `transition_order_status(...)`, and
inbound review diagnostics check the loaded order tenant before acting.

The weakness is that this protection is enforced by caller convention. Future
page, dashboard, or runtime code can still call a broad read and forget to
filter, producing cross-tenant leakage without an error.

Repo facts inspected for this design:

* `pages/3_Dashboard.py` calls `run_locked_dashboard_read_scenario(...)` with a
  `tenant_id`.
* `src/duna_orders/services/dashboard_read_scenario.py` currently calls broad
  `list_orders()`, `list_customers()`, and `list_products(active_only=False)`,
  then filters by tenant.
* `pages/2_Orders_Today.py` calls `storage.list_orders()` and filters today
  orders through `filter_today_orders(..., tenant_id=...)`.
* `pages/1_New_Order.py` calls `storage.list_products(...)` directly for parser
  product context, product selector data, and inventory rows.
* `src/duna_orders/web/inbound.py` calls `storage.list_products(active_only=True)`
  and filters by tenant before parsing inbound messages.
* `src/duna_orders/services/inbound_draft_review.py` uses linked processed
  messages already filtered by tenant, then calls broad `get_order(...)` and
  rejects missing or tenant-mismatched orders.
* `src/duna_orders/services/customer_context.py` already uses tenant-aware
  storage paths: `get_customer_by_phone(..., tenant_id=...)` and
  `get_customer_order_history(customer_id, tenant_id, ...)`.

## 3. Risk Classification

P0:
No active immediate corruption or security issue is known today. Critical
service paths filter by tenant or validate tenant ownership before writes and
actions. This is deliberate hardening, not an emergency fix.

P1:
Likely future leakage risk. Broad reads are available at the boundary, and
future page/dashboard/runtime callers may use them accidentally without tenant
filtering.

P2:
Test, parity, and design debt. There is no structural test proving tenant A
cannot see tenant B rows through page/dashboard/runtime read paths. Any new
scoped contract must preserve backend parity across memory, Postgres, and
Google Sheets.

P3:
Naming and design smell. Methods such as `list_orders()` read as "all orders"
and invite misuse in tenant-scoped product code.

## 4. Locked Direction: E Toward C

Use staged "E toward C".

Immediate direction: Option E stage 1.

Add a tenant-scoped read layer above `StorageInterface`.

Rules:

* `tenant_id` is required, explicit, and not optional.
* `tenant_id` is not defaulted.
* The scoped layer delegates internally to existing broad
  `StorageInterface` reads and filters.
* `StorageInterface` remains unchanged.
* The layer is thin and read-only.
* It is not a repository-pattern rewrite.
* It is not a transaction abstraction.
* It is backend-agnostic at its contract boundary.
* It must be expressible on Postgres, memory, and Google Sheets.
* Implementation efficiency may differ below the layer later, but the public
  contract stays backend-agnostic.

Destination: Option C.

Eventually evolve `StorageInterface` read methods to require `tenant_id` across
all backends. This is the committed destination, but not the first
implementation step.

C is a one-way change and should happen last, when:

* the scoped read contract is stable;
* meaningful callers have already migrated;
* broad read uses are quarantined;
* backend parity is proven;
* the cost of changing the migration boundary is low.

## 5. Why Not Alternatives

Option A: keep `StorageInterface` broad/global and enforce tenant filtering
only in services.

Rejected as an end state. It formalizes the status quo, keeps isolation as
convention, and leaves the P1 leakage risk.

Option B: add a scoped layer without stating the destination.

This is essentially E stage 1 but silent on destination. Choose E so the
destination is explicit and the scoped layer does not ossify into permanent
dual-pathing.

Option C: evolve `StorageInterface` immediately.

Right destination, wrong first move. It creates a big-bang change to the
migration boundary across all backends before the scoped read shape is
validated.

Option D: add Postgres-only tenant-scoped read APIs while keeping Sheets and
memory contract unchanged.

Rejected as the architecture direction. It breaks backend parity at the
interface and reintroduces backend-sniffing in services, fighting the
`StorageInterface`-as-migration-boundary invariant.

Narrow legitimate version:

The scoped layer may internally use Postgres-efficient queries later while
presenting the same backend-agnostic contract. That is an implementation detail
below the layer, not a D-style interface split.

## 6. Standing Invariants

* Storage is pure persistence.
* `StorageInterface` is the migration boundary.
* Do not evolve `StorageInterface` in this design-doc pass.
* The scoped layer is a thin read layer, not a repository-pattern or
  transaction-abstraction overhaul.
* Backend parity matters: scoped reads must be expressible on Postgres, memory,
  and Sheets.
* Domain changes go through Pydantic/domain models.
* Google Sheets headers must stay aligned with
  `src/duna_orders/storage/schema.py`.
* `approved -> confirmed` must only happen through
  `OrderService.confirm_approved_order(...)`.
* Generic `transition_order_status(...)` must not perform
  `approved -> confirmed`.
* Duplicate sale movements must fail hard.
* Legacy `confirm_order(...)` Postgres path must remain refactored onto the
  atomic confirmation core.

## 7. Staged Plan

Stage 1: immediate.

* Add a tenant-scoped read layer above `StorageInterface`.
* Require `tenant_id` on every scoped read.
* Delegate to current broad reads and filter internally.
* Cover only reads used by live page/dashboard/runtime callers today.
* Do not assume all broad methods must be included in stage 1.
* Migrate one most-tenant-sensitive caller as proof-of-use.
* Do not change `StorageInterface`.
* Do not change writes.

Stage 2: follow-on.

* Migrate remaining page/dashboard/runtime read callers to the scoped layer.
* Quarantine broad reads.
* Rename broad reads or mark them clearly internal/dangerous, for example
  `_list_orders_all_tenants` or an equivalent project-appropriate convention.
* Restrict broad reads to the scoped layer plus admin/migration paths.
* Add a guard test or convention check that fails if page/dashboard modules
  import/use broad reads directly.

Stage 3: later destination.

* Evolve `StorageInterface` read methods to require `tenant_id` across all
  backends.
* This is the one-way change.
* Take it only when the scoped contract is stable, callers are migrated, and
  all backends are ready.
* Stages 1 and 2 close the P1 risk.
* Stage 3 is eventual hardening, deferred until cheap.

## 8. Stage-1 Scoped Contract Proposal

Recommended module:

`src/duna_orders/services/tenant_scoped_reads.py`

The name intentionally says what the layer does. It is a service-layer read
adapter over `StorageInterface`, not a storage backend and not a repository
rewrite.

Proposed class:

```python
class TenantScopedReadService:
    def __init__(self, storage: StorageInterface) -> None:
        ...
```

Smallest stage-1 method set based on current live callers:

```python
def list_orders(
    self,
    *,
    tenant_id: str,
    status: str | None = None,
    since: datetime | None = None,
) -> list[Order]:
    ...

def get_order(
    self,
    *,
    tenant_id: str,
    order_id: str,
) -> Order | None:
    ...

def list_products(
    self,
    *,
    tenant_id: str,
    active_only: bool = True,
) -> list[Product]:
    ...

def list_customers(
    self,
    *,
    tenant_id: str,
) -> list[Customer]:
    ...
```

Why these four:

* `list_orders(...)` is used by Today Orders and dashboard data loading.
* `get_order(...)` is high-sensitivity because linked inbound review and
  action services load orders by ID and then tenant-check. Wrapping this shape
  early prevents future detail pages from repeating the broad-read pattern.
* `list_products(...)` is used by New Order, inbound parsing, dashboard, and
  inventory display.
* `list_customers(...)` is used by dashboard data loading.

Why not `list_stock_movements(...)` in Stage 1:

Current live page/dashboard/runtime callers do not use broad
`list_stock_movements(...)` directly. It is used in service confirmation logic,
tests, and storage internals. It remains a future scoped-read candidate, but it
is not needed for the first proof slice.

Optional later method if a live caller appears:

```python
def list_stock_movements(
    self,
    *,
    tenant_id: str,
    product_id: str | None = None,
) -> list[StockMovement]:
    ...
```

Contract rules:

* `tenant_id` must be keyword-only and required.
* No default tenant.
* No optional tenant.
* Empty or whitespace `tenant_id` should raise `ValueError`.
* Returned rows must all have `row.tenant_id == tenant_id`.
* The layer may call existing broad reads internally in Stage 1.
* The layer must not mutate domain objects or storage state.

Implementation sketch:

```python
class TenantScopedReadService:
    def __init__(self, storage: StorageInterface) -> None:
        self._storage = storage

    def list_orders(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        since: datetime | None = None,
    ) -> list[Order]:
        scoped_tenant_id = _require_tenant_id(tenant_id)
        return [
            order
            for order in self._storage.list_orders(status=status, since=since)
            if order.tenant_id == scoped_tenant_id
        ]
```

The sketch is not an implementation request. It documents the intended shape.

## 9. Scoped Layer Location and Consumption Model

Recommended boundary:

* Put the layer in `src/duna_orders/services/tenant_scoped_reads.py`.
* It depends on `StorageInterface`.
* It returns domain models.
* It has no SQLAlchemy, Google Sheets, Streamlit, or backend-specific imports.

Consumption model:

* Page/dashboard/runtime code receives or constructs a
  `TenantScopedReadService` next to existing service setup.
* Streamlit pages stay free of backend-sniffing.
* FastAPI runtime code stays free of backend-sniffing.
* Dashboard scenario loading should use the scoped layer before computing
  dashboard metrics.
* Existing business services may continue to enforce tenant checks on action
  paths. Migrating them is a follow-on choice, not required for the first
  proof slice.

Possible UI setup helper:

```python
def get_tenant_scoped_read_service(
    storage: StorageInterface,
) -> TenantScopedReadService:
    return TenantScopedReadService(storage)
```

This mirrors current service composition in `duna_orders.ui.setup` without
making pages inspect backend types.

## 10. Stage-2 Quarantine Design

After the scoped layer has proof-of-use, broad reads should become hard to
misuse.

Quarantine mechanisms:

* Rename broad read methods where practical, or annotate them clearly as
  all-tenant persistence reads.
* Restrict direct page/dashboard/runtime usage of broad reads.
* Allow broad reads only in:
  * the tenant-scoped read layer;
  * admin/migration/demo seeding paths;
  * storage contract tests;
  * backend implementation internals.
* Add a guard test or convention check that scans page/dashboard/runtime modules
  for direct `.list_orders(`, `.list_products(`, `.list_customers(`, and
  `.get_order(` usage outside approved files.

Suggested guard scope for Stage 2:

* `pages/`;
* `src/duna_orders/web/`;
* dashboard scenario/data-loading modules;
* Streamlit UI setup or page modules.

The guard should not block storage implementations or storage contract tests.

Stage 2A boundary note:

The runtime read guard is convention-enforced by a static test over an explicit
allowlist of migrated runtime read modules. It is not construction-enforced by
types. A green guard proves the listed runtime read paths avoid direct broad
storage reads; it does not prove write paths are tenant-safe and must not be
treated as write-path tenant-safety coverage. Any new page, dashboard, or
runtime read module must be added to the enforced set when it is introduced.
Inbound review may use `get_order_for_diagnostics(...)` as the named
cross-tenant diagnostic exception so missing linked orders and tenant
mismatches remain distinguishable.

Stage 2B-2 boundary note:

Broad cross-tenant storage reads use the `unscoped_` prefix when renamed. The
first application is limited to `unscoped_list_products(...)` and
`unscoped_list_customers(...)` on `StorageInterface` and its backends. The
tenant-scoped service keeps `list_products(...)` and `list_customers(...)` as
stable scoped APIs, delegating internally to the unscoped storage methods. No
old-name aliases are kept. Guarded runtime modules must not call unscoped
storage reads directly. `get_order(...)`, `list_orders(...)`, and
`list_stock_movements(...)` remain deferred because diagnostics/write paths,
Sheets/cache/dashboard churn, and confirmation/stock action logic need separate
slices.

## 11. Stage-3 Trigger Condition

Evolve `StorageInterface` only when all of these are true:

* Scoped contract is stable.
* Meaningful page/dashboard/runtime callers are migrated.
* Page/dashboard/runtime paths no longer use broad reads directly.
* Backend parity is proven on memory and Postgres.
* Sheets compatibility is accounted for, even if live Sheets is not part of the
  immediate default suite.
* Docs and tests make the invariant clear:
  page/dashboard/runtime read paths must use tenant-scoped reads.
* The remaining broad-read use sites are limited to scoped layer,
  admin/migration paths, and tests.
* The change is cheap enough to do as a controlled one-way migration boundary
  update.

Stage 3 should update all backends together:

* `InMemoryStorage`;
* `PostgresStorage`;
* `GoogleSheetsStorage`;
* storage contract tests;
* any admin/demo paths that require all-tenant reads.

## 12. Tests to Add

Tenant isolation structural proof:

* Seed two tenants with overlapping data.
* Call each scoped read as tenant A.
* Assert zero tenant-B rows.
* Use one shared two-tenant fixture reused across scoped reads, not heavy
  per-method reseeding.
* This test is worth landing even before full caller migration because it
  catches leaks regardless of architecture and nothing currently proves
  isolation structurally.

Required `tenant_id`:

* Calling a scoped read without `tenant_id` is a contract/type error.
* Calling with empty or whitespace `tenant_id` raises `ValueError`.
* No scoped read ever silently becomes an all-tenants read.

Backend parity:

* The scoped layer returns equivalent results on memory and Postgres for the
  same two-tenant fixture.
* This proves the layer is not accidentally Postgres-specific.
* Sheets compatibility remains required by contract, even if live Sheets tests
  are not part of the immediate slice.

Stage-2 guard:

* Page/dashboard modules do not import or use broad reads directly.
* This is a later guard test or convention check, not part of Stage 1 unless the
  first implementation slice explicitly includes it.

Regression tests:

* Dashboard scenario returns only tenant rows when backed by the scoped layer.
* Today Orders list excludes other-tenant orders.
* New Order product selector and parser product context exclude other-tenant
  products.
* Order detail scoped read returns `None` for a tenant mismatch.

## 13. Docs and Decisions to Update Later

Update `DECISIONS.md` after implementation starts:

* E-now / C-as-destination.
* Broad reads quarantined in Stage 2.
* `StorageInterface` evolution deferred to Stage 3 with trigger conditions.

Update architecture docs later:

* scoped-layer-above-boundary design;
* explicit note that `StorageInterface` is not evolved yet;
* backend parity remains intact.

New invariant to document later:

Page/dashboard/runtime read paths must use the tenant-scoped read layer; broad
`StorageInterface` reads are restricted to the scoped layer and admin/migration
paths.

Do not update those docs in this design-doc pass.

## 14. First Smallest Safe Implementation Slice

First implementation slice, not built in this pass:

* Add read-only `TenantScopedReadService`.
* Include only:
  * `list_orders(..., tenant_id=...)`;
  * `get_order(..., tenant_id=...)`;
  * `list_products(..., tenant_id=...)`;
  * `list_customers(..., tenant_id=...)`.
* Require explicit `tenant_id` on every method.
* Delegate to existing broad reads and filter internally.
* Add the shared two-tenant isolation test.
* Add required-tenant tests.
* Prove parity on memory and Postgres.
* Migrate one most tenant-sensitive caller as proof-of-use.

Recommended proof caller:

`run_locked_dashboard_read_scenario(...)`.

Reason:

* It currently calls three broad reads and filters manually.
* It feeds the read-only dashboard.
* It is high-risk for silent cross-tenant leakage.
* It already accepts `tenant_id`, so the migration shape is clear.
* It does not touch writes, lifecycle, parser behavior, or outbound behavior.

No Stage 1 writes:

* No `StorageInterface` changes.
* No schema or migration changes.
* No writes.
* No dashboard redesign.
* No caller migration beyond the single proof caller.

## 15. Risks and Non-Goals

Risks:

* The scoped layer can become permanent dual-pathing if Stage 2 and Stage 3 are
  never executed. This is why C is documented as the destination.
* Filtering above broad reads can be less efficient than backend-native tenant
  queries. This is acceptable for Stage 1 because the immediate goal is
  structural safety and contract shape. Backend-efficient internals can come
  later without changing the scoped contract.
* Guard tests can become noisy if they are too broad. Stage 2 should target
  page/dashboard/runtime modules, not storage implementations or contract
  tests.

Non-goals:

* No `StorageInterface` change in this work. Stage 3 only, later.
* No Postgres-only interface split.
* No write-path tenant scoping. Reads only.
* No broad transaction/storage/repository abstraction rewrite.
* No multi-tenant runtime design for how `tenant_id` is resolved from request
  context. This scopes reads given a tenant ID.
* No outbound/customer messaging.
* No payment status enforcement.
* No inbound media/comprobante handling.
* No cancellation stock reversal.
* No duplicate movement repair or idempotency.
* No auto-confirmation.
* No queue or worker behavior.
* No parser changes or `PROMPT_VERSION` bump.
* No dashboard redesign.
