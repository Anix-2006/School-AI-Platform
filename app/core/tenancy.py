"""Multi-tenant isolation pattern. The tenant comes from the JWT and every
workbook read filters rows by tenant_id in application code (see
get_current_tenant and app/services/excel_store.py)."""

from fastapi import Depends

from app.core.security import decode_token


def get_current_tenant(payload: dict = Depends(decode_token)) -> str:
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise ValueError("token missing tenant_id claim")
    return tenant_id
