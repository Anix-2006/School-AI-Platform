"""Multi-tenant isolation pattern. Dev version filters by tenant_id in
application code (see get_current_tenant). For production Postgres,
replace/augment with row-level security policies, e.g.:

    ALTER TABLE students ENABLE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON students
        USING (tenant_id = current_setting('app.current_tenant')::text);

and set `app.current_tenant` per-connection at request start, so a bug
in application-layer filtering can't leak cross-tenant data."""

from fastapi import Depends

from app.core.security import decode_token


def get_current_tenant(payload: dict = Depends(decode_token)) -> str:
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise ValueError("token missing tenant_id claim")
    return tenant_id
