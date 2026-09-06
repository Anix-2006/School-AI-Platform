from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.student import Student
from app.schemas.student import StudentCreate, StudentOut
from app.core.tenancy import get_current_tenant

router = APIRouter(prefix="/students", tags=["students"])


@router.post("", response_model=StudentOut)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    existing = db.query(Student).filter(Student.id == payload.id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student '{payload.id}' already exists. Use a new Student ID or refresh the list.",
        )
    student = Student(tenant_id=tenant_id, **payload.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@router.get("", response_model=list[StudentOut])
def list_students(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    return db.query(Student).filter(Student.tenant_id == tenant_id, Student.active == True).all()  # noqa: E712
