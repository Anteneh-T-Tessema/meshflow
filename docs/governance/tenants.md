# Multi-Tenant Isolation

MeshFlow provides hard data boundaries between tenants through thread-local context, scoped database paths, and a guard middleware that rejects requests without a valid tenant binding.

```python
from meshflow.tenant.store import TenantContext, TenantStore, TenantGuard, scoped_db_path

# Register a tenant
store = TenantStore("meshflow_tenants.db")
tenant = store.create(name="Acme Corp", slug="acme", plan="enterprise")

# Bind the active tenant for the current thread
TenantContext.set(tenant.tenant_id)

# Derive a scoped database path for any store
ledger_path = scoped_db_path("meshflow_runs.db", tenant.tenant_id)
# "meshflow_runs_<first8chars>.db"
```

## `TenantContext` — Thread-Local Binding

`TenantContext` uses `threading.local` so each worker thread or async task holds its own binding. It does not propagate across threads automatically — set it at the top of each request handler.

```python
TenantContext.set(tenant_id)      # bind for this thread
TenantContext.get()               # returns tenant_id or None
TenantContext.require()           # returns tenant_id or raises RuntimeError
TenantContext.clear()             # unbind
```

## `TenantStore` — Tenant Registry

```python
store = TenantStore("meshflow_tenants.db")

tenant = store.create("Acme Corp", "acme", plan="enterprise")
tenant = store.get(tenant_id)
tenant = store.get_by_slug("acme")

tenants = store.list_tenants(status="active")   # status: "active" | "suspended" | "deleted"

store.update_status(tenant_id, "suspended")
store.update_plan(tenant_id, "pro")
store.delete(tenant_id)
```

Valid plans: `"free"`, `"pro"`, `"enterprise"`. Valid statuses: `"active"`, `"suspended"`, `"deleted"`.

`Tenant.is_active` returns `True` only when `status == "active"`.

## `TenantGuard` — Request Middleware

`TenantGuard` validates the active context against the registry. Call it at the start of any request that must be tenant-scoped.

```python
guard = TenantGuard(store)

tenant = guard.check()                  # uses TenantContext.get()
tenant = guard.check(tenant_id="acme-uuid")   # explicit override

# Raises PermissionError if:
#   - No tenant context is set
#   - Tenant ID is not found in the registry
#   - Tenant is suspended or deleted
```

## `scoped_db_path()` — Per-Tenant Database Paths

Every MeshFlow store (ledger, vault, SLA, policy) can be namespaced per tenant by using `scoped_db_path`:

```python
from meshflow.tenant.store import scoped_db_path
from meshflow.core.ledger import ReplayLedger

ledger_path = scoped_db_path("meshflow_runs.db", tenant.tenant_id)
# "meshflow_runs_a1b2c3d4.db"  (first 8 chars of UUID)

ledger = ReplayLedger(ledger_path, tenant_id=tenant.tenant_id)
```

`":memory:"` is passed through unchanged, enabling in-process multi-tenant tests.

## Per-Tenant Ledger Isolation

`ReplayLedger` accepts a `tenant_id` parameter that filters all queries to that namespace. A single shared database can host multiple tenants, or each tenant can have a fully separate database file.

```python
ledger_acme = ReplayLedger("meshflow_runs.db", tenant_id=acme.tenant_id)
ledger_beta = ReplayLedger("meshflow_runs.db", tenant_id=beta.tenant_id)

# list_runs() and get_run() are scoped — no cross-tenant data leakage
```

GDPR right-to-erasure at the tenant level:

```python
rows_deleted = await ledger.delete_tenant(tenant_id)
```

## `meshflow tenant` CLI

```bash
# Create a tenant
meshflow tenant create --name "Acme Corp" --slug acme --plan enterprise

# List all tenants
meshflow tenant list
meshflow tenant list --status suspended

# Get tenant details by slug
meshflow tenant get --slug acme

# Suspend a tenant
meshflow tenant suspend --slug acme

# Change plan
meshflow tenant plan --slug acme --plan pro
```

All commands default to `meshflow_tenants.db` and accept `--db` to override.
