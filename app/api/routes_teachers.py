from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.config import settings
from app.core.tenancy import get_current_tenant
from app.schemas.teacher import (
    SyllabusRowOut,
    TeacherMeOut,
    TeacherStudentOut,
    TeacherSubjectOut,
    TeacherTaskOut,
    TeacherTaskPatch,
    TimetableSlotOut,
)
from app.services import excel_teacher_store as store
from app.services.excel_teacher_store import DEFAULT_TEACHER_ID

router = APIRouter(prefix="/teachers", tags=["teachers"])


def resolve_teacher_id(
    teacher_id: str | None = Query(
        None, description="Demo identity. Falls back to X-Teacher-Id or DEFAULT_TEACHER_ID."
    ),
    x_teacher_id: str | None = Header(None, alias="X-Teacher-Id"),
) -> str:
    # Demo-only: no teacher JWT yet. Identity is query, header, or config default.
    return teacher_id or x_teacher_id or settings.default_teacher_id or DEFAULT_TEACHER_ID


def require_teacher(tenant_id: str, teacher_id: str) -> dict:
    teacher = store.get_teacher(tenant_id, teacher_id)
    if not teacher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found")
    return teacher


@router.get("/me", response_model=TeacherMeOut)
def teacher_me(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    profile = store.teacher_profile(tenant_id, teacher_id)
    return TeacherMeOut(
        id=profile["id"],
        name=profile["name"],
        email=profile["email"],
        role=profile["role"],
        subjects=[TeacherSubjectOut(**s) for s in profile["subjects"]],
    )


@router.get("/me/timetable", response_model=list[TimetableSlotOut])
def teacher_timetable(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return [TimetableSlotOut(**s) for s in store.teacher_timetable(tenant_id, teacher_id)]


@router.get("/me/tasks", response_model=list[TeacherTaskOut])
def teacher_tasks(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return [TeacherTaskOut(**t) for t in store.teacher_tasks(tenant_id, teacher_id)]


@router.patch("/me/tasks/{task_id}", response_model=TeacherTaskOut)
def patch_teacher_task(
    task_id: str,
    payload: TeacherTaskPatch,
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    if payload.status not in ("todo", "done"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be todo or done",
        )
    require_teacher(tenant_id, teacher_id)
    task = store.patch_task(tenant_id, teacher_id, task_id, payload.status)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TeacherTaskOut(**task)


@router.get("/me/syllabus", response_model=list[SyllabusRowOut])
def teacher_syllabus(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return [SyllabusRowOut(**r) for r in store.teacher_syllabus(tenant_id, teacher_id)]


@router.get("/me/students", response_model=list[TeacherStudentOut])
def teacher_students(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return [TeacherStudentOut(**s) for s in store.teacher_students(tenant_id, teacher_id)]
