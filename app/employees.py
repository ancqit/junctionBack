from datetime import date, datetime, timezone
from enum import Enum

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .database import employees
from .utils import parse_object_id

router = APIRouter(prefix="/employees", tags=["employees"])


class EmploymentStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    on_leave = "on_leave"
    terminated = "terminated"


class EmploymentType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    temporary = "temporary"


class EmployeeAddress(BaseModel):
    line1: str = Field(min_length=1, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(default="IN", min_length=2, max_length=2)

    @field_validator("line1", "city", "state", "postal_code")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EmergencyContact(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    relationship: str = Field(min_length=1, max_length=80)
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")

    @field_validator("name", "relationship")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class EmployeeCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=80)
    employee_code: str = Field(min_length=1, max_length=40)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    phone_number: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    role: str = Field(min_length=1, max_length=80)
    department: str = Field(min_length=1, max_length=80)
    employment_type: EmploymentType
    status: EmploymentStatus = EmploymentStatus.active
    hire_date: date
    termination_date: date | None = None
    manager_id: str | None = None
    salary: float | None = Field(default=None, ge=0)
    address: EmployeeAddress | None = None
    emergency_contact: EmergencyContact | None = None
    notes: str | None = Field(default=None, max_length=2000)
    avatar_url: HttpUrl | None = None

    @field_validator("employee_code", "first_name", "last_name", "role", "department")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("termination_date")
    @classmethod
    def termination_after_hire(cls, value: date | None, info) -> date | None:
        hire_date = info.data.get("hire_date")
        if value is not None and hire_date is not None and value < hire_date:
            raise ValueError("termination_date must be on or after hire_date")
        return value


class EmployeeUpdate(BaseModel):
    employee_code: str | None = Field(default=None, min_length=1, max_length=40)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: EmailStr | None = None
    phone_number: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    role: str | None = Field(default=None, min_length=1, max_length=80)
    department: str | None = Field(default=None, min_length=1, max_length=80)
    employment_type: EmploymentType | None = None
    status: EmploymentStatus | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    manager_id: str | None = None
    salary: float | None = Field(default=None, ge=0)
    address: EmployeeAddress | None = None
    emergency_contact: EmergencyContact | None = None
    notes: str | None = Field(default=None, max_length=2000)
    avatar_url: HttpUrl | None = None

    @field_validator("employee_code", "first_name", "last_name", "role", "department")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Employee(EmployeeCreate):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    created_at: datetime
    updated_at: datetime


def serialize_employee(document: dict) -> Employee:
    return Employee(
        id=str(document["_id"]),
        store_id=document["store_id"],
        employee_code=document["employee_code"],
        first_name=document["first_name"],
        last_name=document["last_name"],
        email=document.get("email"),
        phone_number=document["phone_number"],
        role=document["role"],
        department=document["department"],
        employment_type=document["employment_type"],
        status=document.get("status", EmploymentStatus.active.value),
        hire_date=document["hire_date"],
        termination_date=document.get("termination_date"),
        manager_id=document.get("manager_id"),
        salary=document.get("salary"),
        address=document.get("address"),
        emergency_contact=document.get("emergency_contact"),
        notes=document.get("notes"),
        avatar_url=document.get("avatar_url"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


@router.get("", response_model=list[Employee])
def list_employees(
    store_id: str | None = Query(default=None, min_length=1, max_length=80),
    status: EmploymentStatus | None = None,
    department: str | None = Query(default=None, min_length=1, max_length=80),
) -> list[Employee]:
    query: dict = {}
    if store_id:
        query["store_id"] = store_id
    if status:
        query["status"] = status.value
    if department:
        query["department"] = department.strip()
    documents = employees.find(query).sort("created_at", -1)
    return [serialize_employee(document) for document in documents]


@router.post("", response_model=Employee, status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeCreate) -> Employee:
    employees.create_index([("store_id", 1), ("employee_code", 1)], unique=True)
    if payload.manager_id is not None:
        manager = employees.find_one({"_id": parse_object_id(payload.manager_id, "Employee")})
        if manager is None:
            raise HTTPException(status_code=400, detail="manager_id does not reference an existing employee")

    now = datetime.now(timezone.utc)
    document = {**payload.model_dump(mode="json"), "created_at": now, "updated_at": now}
    try:
        result = employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An employee with this code already exists for the store")
    document["_id"] = result.inserted_id
    return serialize_employee(document)


@router.put("/{employee_id}", response_model=Employee)
def update_employee(employee_id: str, payload: EmployeeUpdate) -> Employee:
    changes = payload.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    if "manager_id" in changes and changes["manager_id"] is not None:
        manager = employees.find_one({"_id": parse_object_id(changes["manager_id"], "Employee")})
        if manager is None:
            raise HTTPException(status_code=400, detail="manager_id does not reference an existing employee")

    if changes.get("termination_date") is not None:
        existing = employees.find_one({"_id": parse_object_id(employee_id, "Employee")})
        if existing is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        hire_date = changes.get("hire_date", existing.get("hire_date"))
        termination_date = changes["termination_date"]
        if hire_date is not None and termination_date < hire_date:
            raise HTTPException(status_code=400, detail="termination_date must be on or after hire_date")

    changes["updated_at"] = datetime.now(timezone.utc)
    try:
        document = employees.find_one_and_update(
            {"_id": parse_object_id(employee_id, "Employee")},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An employee with this code already exists for the store")
    if document is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return serialize_employee(document)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(employee_id: str) -> Response:
    result = employees.delete_one({"_id": parse_object_id(employee_id, "Employee")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
