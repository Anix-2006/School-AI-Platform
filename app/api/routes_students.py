from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.student import AGE_TIERS, GuardianOut, StudentCreate, StudentOut
from app.core.tenancy import get_current_tenant
from app.services.excel_store import WorkbookLockedError, get_store

router = APIRouter(tags=["students"])


@router.post("/students", response_model=StudentOut)
def create_student(payload: StudentCreate, tenant_id: str = Depends(get_current_tenant)):
    if payload.age_tier not in AGE_TIERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"age_tier must be one of: {', '.join(AGE_TIERS)}",
        )
    store = get_store()
    if store.get("students", payload.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student '{payload.id}' already exists. Use a new Student ID or refresh the list.",
        )
    row = {
        **payload.model_dump(),
        "date_of_birth": payload.date_of_birth.isoformat() if payload.date_of_birth else None,
        "tenant_id": tenant_id,
        "active": True,
    }
    try:
        return store.append_row("students", row)
    except WorkbookLockedError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None


@router.get("/students", response_model=list[StudentOut])
def list_students(tenant_id: str = Depends(get_current_tenant)):
    return get_store().students(tenant_id)


@router.get("/guardians", response_model=list[GuardianOut])
def list_guardians(tenant_id: str = Depends(get_current_tenant)):
    """Guardians with their linked child and consent status."""
    store = get_store()
    students = {s["id"]: s for s in store.students(tenant_id, active_only=False)}
    out = []
    for g in store.guardians(tenant_id):
        child = students[g["student_id"]]
        out.append(GuardianOut(
            id=g["id"],
            name=g["name"],
            relation=g.get("relation"),
            student_id=child["id"],
            student_name=child["name"],
            grade=child["grade"],
            section=child.get("section"),
            preferred_language=g.get("preferred_language") or "en",
            whatsapp_number=g.get("whatsapp_number"),
            has_consent=store.has_consent(g),
        ))
    out.sort(key=lambda g: (g.student_name, g.name))
    return out
