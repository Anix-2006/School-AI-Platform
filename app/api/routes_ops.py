from fastapi import APIRouter, Depends

from app.core.tenancy import get_current_tenant
from app.tasks.daily_batch import run_daily_updates, run_insight_scan

router = APIRouter(prefix="/ops", tags=["ops"])


@router.post("/daily-batch")
async def trigger_daily_batch(tenant_id: str = Depends(get_current_tenant)):
    """Manual trigger for the daily guardian update job (local demo)."""
    return await run_daily_updates(tenant_id)


@router.post("/insight-scan")
def trigger_insight_scan(tenant_id: str = Depends(get_current_tenant)):
    """Manual trigger for the nightly risk-alert scan (local demo)."""
    return {"alerts": run_insight_scan(tenant_id)}
