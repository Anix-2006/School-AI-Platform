from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.config import settings
from app.core.tenancy import get_current_tenant
from app.schemas.teacher import (
    SyllabusRowOut,
    TeacherListItem,
    TeacherMeOut,
    TeacherStudentOut,
    TeacherTaskOut,
    TeacherTaskPatch,
    TimetableSlotOut,
)
from app.services import teacher_service
from app.services.excel_store import WorkbookLockedError, get_store

router = APIRouter(prefix="/teachers", tags=["teachers"])


def resolve_teacher_id(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str | None = Query(
        None, description="Demo identity. Falls back to X-Teacher-Id, DEFAULT_TEACHER_ID, then the first teacher."
    ),
    x_teacher_id: str | None = Header(None, alias="X-Teacher-Id"),
) -> str:
    # Demo-only: no teacher JWT yet.
    chosen = teacher_id or x_teacher_id or teacher_service.default_teacher_id(tenant_id, settings.default_teacher_id)
    if not chosen:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No teachers in the workbook")
    return chosen


def require_teacher(tenant_id: str, teacher_id: str) -> dict:
    teacher = get_store().teacher(teacher_id, tenant_id)
    if not teacher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Teacher not found")
    return teacher


@router.get("", response_model=list[TeacherListItem])
def list_teachers(tenant_id: str = Depends(get_current_tenant)):
    return teacher_service.list_teachers(tenant_id)


@router.get("/me", response_model=TeacherMeOut)
def teacher_me(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    return teacher_service.profile(require_teacher(tenant_id, teacher_id))


@router.get("/me/timetable", response_model=list[TimetableSlotOut])
def teacher_timetable(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return teacher_service.timetable(teacher_id)


@router.get("/me/tasks", response_model=list[TeacherTaskOut])
def teacher_tasks(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return teacher_service.tasks(teacher_id)


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
    try:
        task = teacher_service.set_task_status(teacher_id, task_id, payload.status)
    except WorkbookLockedError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("/me/syllabus", response_model=list[SyllabusRowOut])
def teacher_syllabus(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return teacher_service.syllabus(tenant_id, teacher_id)


@router.get("/me/students", response_model=list[TeacherStudentOut])
def teacher_students(
    tenant_id: str = Depends(get_current_tenant),
    teacher_id: str = Depends(resolve_teacher_id),
):
    require_teacher(tenant_id, teacher_id)
    return teacher_service.students(tenant_id, teacher_id)
