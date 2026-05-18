from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, Sequence, String, Text, create_engine, func
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

APP_VERSION = "0.8.0-snowflake-ready"
VALID_PATHCODES = {"DV", "PY", "QA", "PD"}
TERMINAL_REQUEST_STATUSES = {"COMPLETED", "REJECTED", "TERMINATED", "CANCELLED", "EXPIRED"}


def build_database_url() -> str:
    """Build a SQLAlchemy database URL.

    Default remains SQLite for local development.
    Set APP_DATABASE_BACKEND=snowflake to use Snowflake.
    """
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    backend = os.getenv("APP_DATABASE_BACKEND", "sqlite").lower().strip()
    if backend != "snowflake":
        return "sqlite:///./jde_workflow.db"

    try:
        from snowflake.sqlalchemy import URL
    except ImportError as exc:
        raise RuntimeError(
            "Snowflake mode requires snowflake-sqlalchemy. "
            "Install with: pip install snowflake-sqlalchemy snowflake-connector-python"
        ) from exc

    required = [
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
        "SNOWFLAKE_WAREHOUSE",
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing Snowflake environment variables: " + ", ".join(missing))

    kwargs = {
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "database": os.getenv("SNOWFLAKE_DATABASE"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
    }

    role = os.getenv("SNOWFLAKE_ROLE")
    if role:
        kwargs["role"] = role

    return URL(**kwargs)


DATABASE_URL = build_database_url()
IS_SQLITE = str(DATABASE_URL).startswith("sqlite")
IS_SNOWFLAKE = str(DATABASE_URL).startswith("snowflake")

engine_kwargs: Dict[str, Any] = {"pool_pre_ping": True}
if IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="JDE Workflow API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PersonIn(BaseModel):
    email: str = ""
    display_name: str = ""


class RequestCreate(BaseModel):
    jira_project: str = ""
    jira_ticket_url: str = ""
    omw_project: str = ""
    omw_project_desc: str = ""
    jde_pathcodes: str = ""
    developer: PersonIn = Field(default_factory=PersonIn)
    requestor: PersonIn = Field(default_factory=PersonIn)
    business_manager: PersonIn = Field(default_factory=PersonIn)
    technical_manager: PersonIn = Field(default_factory=PersonIn)
    project_manager: PersonIn = Field(default_factory=PersonIn)
    peer_review: PersonIn = Field(default_factory=PersonIn)
    cnc: PersonIn = Field(default_factory=PersonIn)
    cnc2: PersonIn = Field(default_factory=PersonIn)
    emergency: str = "No"
    sox_sod: str = "Yes"
    special_instructions: str = ""


class StepAction(BaseModel):
    actor_email: str = ""
    action: str = ""
    comments: str = ""


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, Sequence("audit_events_id_seq"), primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("requests.id"), index=True, nullable=False)
    event_type = Column(String(80), nullable=False)
    actor_email = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    request = relationship("WorkflowRequest", back_populates="audit_events")


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id = Column(Integer, Sequence("workflow_steps_id_seq"), primary_key=True, index=True)
    request_id = Column(Integer, ForeignKey("requests.id"), index=True, nullable=False)
    sequence_no = Column(Integer, nullable=False)
    role_name = Column(String(120), nullable=False)
    step_type = Column(String(80), nullable=False)
    environment_code = Column(String(20), nullable=True)
    assigned_email = Column(String(500), nullable=False)
    assigned_name = Column(String(255), nullable=True)
    status = Column(String(60), default="PENDING", nullable=False)
    response = Column(String(60), nullable=True)
    comments = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    request = relationship("WorkflowRequest", back_populates="steps")


class WorkflowRequest(Base):
    __tablename__ = "requests"

    id = Column(Integer, Sequence("requests_id_seq"), primary_key=True, index=True)
    request_number = Column(Integer, unique=True, index=True, nullable=False)
    jira_project = Column(String(120), nullable=False)
    jira_ticket_url = Column(String(500), nullable=False)
    omw_project = Column(String(120), nullable=False)
    omw_project_desc = Column(Text, nullable=False)
    environment_string = Column(String(120), nullable=False)
    emergency = Column(String(20), default="No", nullable=False)
    sox_sod = Column(String(20), default="Yes", nullable=False)
    special_instructions = Column(Text, nullable=True)
    status = Column(String(80), default="DRAFT", nullable=False)
    current_stage = Column(String(160), default="Draft", nullable=False)
    hold_step_id = Column(Integer, nullable=True)

    developer_email = Column(String(255), nullable=False)
    developer_name = Column(String(255), nullable=False)
    requestor_email = Column(String(255), nullable=False)
    requestor_name = Column(String(255), nullable=False)
    business_manager_email = Column(String(255), nullable=True)
    business_manager_name = Column(String(255), nullable=True)
    technical_manager_email = Column(String(255), nullable=False)
    technical_manager_name = Column(String(255), nullable=False)
    project_manager_email = Column(String(255), nullable=True)
    project_manager_name = Column(String(255), nullable=True)
    peer_review_email = Column(String(255), nullable=True)
    peer_review_name = Column(String(255), nullable=True)
    cnc_email = Column(String(255), nullable=False)
    cnc_name = Column(String(255), nullable=False)
    cnc2_email = Column(String(255), nullable=False)
    cnc2_name = Column(String(255), nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    steps = relationship("WorkflowStep", back_populates="request", cascade="all, delete-orphan", order_by="WorkflowStep.sequence_no")
    audit_events = relationship("AuditEvent", back_populates="request", cascade="all, delete-orphan", order_by="AuditEvent.created_at")


# Prototype mode convenience. For production, use Alembic migrations instead.
if os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true":
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    text = normalize_email(value)
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", text))


def is_valid_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def parse_pathcodes(value: str) -> List[str]:
    raw = str(value or "")
    pathcodes = []
    for item in raw.split(","):
        code = item.strip().upper()
        if code and code not in pathcodes:
            pathcodes.append(code)
    return pathcodes


def validate_create_payload(payload: RequestCreate) -> List[str]:
    errors: List[str] = []

    required_text = {
        "jira_project": "Jira Ticket is required.",
        "jira_ticket_url": "Jira URL is required.",
        "omw_project": "OMW Project is required.",
        "omw_project_desc": "OMW Project Description is required.",
    }

    for field_name, message in required_text.items():
        if not str(getattr(payload, field_name, "") or "").strip():
            errors.append(message)

    if payload.jira_ticket_url and not is_valid_url(payload.jira_ticket_url):
        errors.append("Jira URL must be a valid http:// or https:// URL.")

    pathcodes = parse_pathcodes(payload.jde_pathcodes)
    if not pathcodes:
        errors.append("At least one JDE Pathcode is required.")

    invalid_pathcodes = [code for code in pathcodes if code not in VALID_PATHCODES]
    if invalid_pathcodes:
        errors.append(f"Invalid JDE Pathcode(s): {', '.join(invalid_pathcodes)}.")

    people_fields = [
        ("developer", "Developer", True),
        ("requestor", "Requestor", True),
        ("business_manager", "Business Manager", False),
        ("technical_manager", "Technical Manager", True),
        ("project_manager", "Project Manager", False),
        ("peer_review", "Peer Reviewer", False),
        ("cnc", "CNC", True),
        ("cnc2", "CNC2", True),
    ]

    seen_emails: Dict[str, str] = {}

    for field_name, label, required in people_fields:
        person = getattr(payload, field_name, None)
        display_name = str(getattr(person, "display_name", "") or "").strip() if person else ""
        email = normalize_email(getattr(person, "email", "") if person else "")

        if required and not display_name:
            errors.append(f"{label} name is required.")

        if required and not email:
            errors.append(f"{label} email is required.")
        elif email and not is_valid_email(email):
            errors.append(f"{label} email is invalid.")

        if email and is_valid_email(email):
            if email in seen_emails:
                errors.append(f"{label} email duplicates {seen_emails[email]}.")
            else:
                seen_emails[email] = label

    return errors


def raise_if_validation_errors(errors: List[str]) -> None:
    if errors:
        raise HTTPException(status_code=422, detail={"message": "Request validation failed.", "errors": errors})


def next_request_number(db: Session) -> int:
    max_number = db.query(func.max(WorkflowRequest.request_number)).scalar()
    return int(max_number or 1000) + 1


def add_audit(db: Session, request_id: int, event_type: str, message: str, actor_email: Optional[str] = None) -> None:
    db.add(AuditEvent(request_id=request_id, event_type=event_type, message=message, actor_email=actor_email))


def person_email(payload: RequestCreate, field: str) -> str:
    return normalize_email(getattr(getattr(payload, field), "email", ""))


def person_name(payload: RequestCreate, field: str) -> str:
    return str(getattr(getattr(payload, field), "display_name", "") or "").strip()


def build_request_from_payload(db: Session, payload: RequestCreate) -> WorkflowRequest:
    pathcodes = parse_pathcodes(payload.jde_pathcodes)
    req = WorkflowRequest(
        request_number=next_request_number(db),
        jira_project=payload.jira_project.strip(),
        jira_ticket_url=payload.jira_ticket_url.strip(),
        omw_project=payload.omw_project.strip(),
        omw_project_desc=payload.omw_project_desc.strip(),
        environment_string=",".join(pathcodes),
        emergency=payload.emergency or "No",
        sox_sod=payload.sox_sod or "Yes",
        special_instructions=payload.special_instructions or "",
        developer_email=person_email(payload, "developer"),
        developer_name=person_name(payload, "developer"),
        requestor_email=person_email(payload, "requestor"),
        requestor_name=person_name(payload, "requestor"),
        business_manager_email=person_email(payload, "business_manager"),
        business_manager_name=person_name(payload, "business_manager"),
        technical_manager_email=person_email(payload, "technical_manager"),
        technical_manager_name=person_name(payload, "technical_manager"),
        project_manager_email=person_email(payload, "project_manager"),
        project_manager_name=person_name(payload, "project_manager"),
        peer_review_email=person_email(payload, "peer_review"),
        peer_review_name=person_name(payload, "peer_review"),
        cnc_email=person_email(payload, "cnc"),
        cnc_name=person_name(payload, "cnc"),
        cnc2_email=person_email(payload, "cnc2"),
        cnc2_name=person_name(payload, "cnc2"),
    )
    db.add(req)
    db.flush()
    create_initial_steps(db, req)
    add_audit(db, req.id, "REQUEST_CREATED", f"REQ-{req.request_number} created.", req.developer_email)
    return req


def add_step(
    db: Session,
    req: WorkflowRequest,
    sequence_no: int,
    role_name: str,
    step_type: str,
    assigned_email: str,
    assigned_name: str = "",
    environment_code: Optional[str] = None,
    status: str = "PENDING",
) -> WorkflowStep:
    step = WorkflowStep(
        request_id=req.id,
        sequence_no=sequence_no,
        role_name=role_name,
        step_type=step_type,
        assigned_email=assigned_email,
        assigned_name=assigned_name,
        environment_code=environment_code,
        status=status,
    )
    db.add(step)
    return step


def create_initial_steps(db: Session, req: WorkflowRequest) -> None:
    seq = 10
    add_step(db, req, seq, "Developer", "APPROVAL", req.developer_email, req.developer_name)
    seq += 10

    if req.peer_review_email:
        add_step(db, req, seq, "Peer Reviewer", "APPROVAL", req.peer_review_email, req.peer_review_name)
        seq += 10

    add_step(db, req, seq, "Requestor", "APPROVAL", req.requestor_email, req.requestor_name)
    seq += 10

    if req.business_manager_email:
        add_step(db, req, seq, "Business Manager", "APPROVAL", req.business_manager_email, req.business_manager_name)
        seq += 10

    add_step(db, req, seq, "Technical Manager", "APPROVAL", req.technical_manager_email, req.technical_manager_name)
    seq += 10

    if req.project_manager_email:
        add_step(db, req, seq, "Project Manager", "APPROVAL", req.project_manager_email, req.project_manager_name)
        seq += 10

    for code in parse_pathcodes(req.environment_string):
        add_step(db, req, seq, f"CNC-{code}", "CNC_COMPLETION", req.cnc_email, req.cnc_name, environment_code=code, status="PENDING_CNC")
        seq += 10
        add_step(db, req, seq, f"CNC2-{code}", "CNC_COMPLETION", req.cnc2_email, req.cnc2_name, environment_code=code, status="PENDING_CNC")
        seq += 10


def serialize_step(step: WorkflowStep) -> Dict[str, Any]:
    return {
        "id": step.id,
        "sequence_no": step.sequence_no,
        "role_name": step.role_name,
        "step_type": step.step_type,
        "environment_code": step.environment_code,
        "assigned_email": step.assigned_email,
        "assigned_name": step.assigned_name,
        "status": step.status,
        "response": step.response,
        "comments": step.comments,
        "started_at": iso(step.started_at),
        "responded_at": iso(step.responded_at),
        "created_at": iso(step.created_at),
    }


def serialize_request(req: WorkflowRequest, include_steps: bool = True) -> Dict[str, Any]:
    data = {
        "id": req.id,
        "request_number": req.request_number,
        "jira_project": req.jira_project,
        "jira_ticket_url": req.jira_ticket_url,
        "omw_project": req.omw_project,
        "omw_project_desc": req.omw_project_desc,
        "environment_string": req.environment_string,
        "emergency": req.emergency,
        "sox_sod": req.sox_sod,
        "special_instructions": req.special_instructions,
        "status": req.status,
        "current_stage": req.current_stage,
        "created_at": iso(req.created_at),
        "submitted_at": iso(req.submitted_at),
        "completed_at": iso(req.completed_at),
        "updated_at": iso(req.updated_at),
        "developer": {"email": req.developer_email, "display_name": req.developer_name},
        "requestor": {"email": req.requestor_email, "display_name": req.requestor_name},
        "business_manager": {"email": req.business_manager_email, "display_name": req.business_manager_name},
        "technical_manager": {"email": req.technical_manager_email, "display_name": req.technical_manager_name},
        "project_manager": {"email": req.project_manager_email, "display_name": req.project_manager_name},
        "peer_review": {"email": req.peer_review_email, "display_name": req.peer_review_name},
        "cnc": {"email": req.cnc_email, "display_name": req.cnc_name},
        "cnc2": {"email": req.cnc2_email, "display_name": req.cnc2_name},
    }
    if include_steps:
        data["steps"] = [serialize_step(step) for step in sorted(req.steps, key=lambda s: s.sequence_no)]
    return data


def serialize_audit(event: AuditEvent) -> Dict[str, Any]:
    return {
        "id": event.id,
        "request_id": event.request_id,
        "event_type": event.event_type,
        "actor_email": event.actor_email,
        "message": event.message,
        "created_at": iso(event.created_at),
    }


def get_request_or_404(db: Session, request_id: int) -> WorkflowRequest:
    req = db.query(WorkflowRequest).filter(WorkflowRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


def get_step_or_404(db: Session, request_id: int, step_id: int) -> WorkflowStep:
    step = db.query(WorkflowStep).filter(WorkflowStep.request_id == request_id, WorkflowStep.id == step_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found.")
    return step


def active_step(req: WorkflowRequest) -> Optional[WorkflowStep]:
    active = [s for s in req.steps if s.status == "ACTIVE"]
    return sorted(active, key=lambda s: s.sequence_no)[0] if active else None


def next_pending_approval(req: WorkflowRequest) -> Optional[WorkflowStep]:
    pending = [s for s in req.steps if s.step_type == "APPROVAL" and s.status == "PENDING"]
    return sorted(pending, key=lambda s: s.sequence_no)[0] if pending else None


def next_pending_cnc(req: WorkflowRequest) -> Optional[WorkflowStep]:
    pending = [s for s in req.steps if s.step_type == "CNC_COMPLETION" and s.status == "PENDING_CNC"]
    return sorted(pending, key=lambda s: s.sequence_no)[0] if pending else None


def activate_step(req: WorkflowRequest, step: WorkflowStep) -> None:
    step.status = "ACTIVE"
    step.started_at = now_utc()
    req.current_stage = step.role_name


def advance_after_approval(req: WorkflowRequest) -> None:
    next_step = next_pending_approval(req)
    if next_step:
        activate_step(req, next_step)
        req.status = "IN_APPROVAL"
        return
    req.status = "APPROVED_PENDING_CNC"
    req.current_stage = "Approved - Pending CNC"


def complete_if_cnc_done(req: WorkflowRequest) -> None:
    remaining = [s for s in req.steps if s.step_type == "CNC_COMPLETION" and s.status in {"PENDING_CNC", "ACTIVE"}]
    if remaining:
        next_step = next_pending_cnc(req)
        if next_step and not active_step(req):
            activate_step(req, next_step)
            req.status = "CNC_IN_PROGRESS"
        return
    req.status = "COMPLETED"
    req.current_stage = "Completed"
    req.completed_at = now_utc()


def audit_export_rows(db: Session) -> List[Dict[str, Any]]:
    rows = []
    requests = db.query(WorkflowRequest).order_by(WorkflowRequest.created_at.desc()).all()
    for req in requests:
        for step in req.steps:
            rows.append(
                {
                    "request_number": req.request_number,
                    "jira_ticket": req.jira_project,
                    "jde_project": req.omw_project,
                    "project_name": req.omw_project_desc,
                    "pathcodes": step.environment_code or req.environment_string,
                    "approver_name": step.assigned_name,
                    "approver_email": step.assigned_email,
                    "role": step.role_name,
                    "response": step.response or step.status,
                    "completed": iso(step.responded_at),
                    "emergency_build": req.emergency,
                    "segregation_of_duty": req.sox_sod,
                    "request_status": req.status,
                    "created_at": iso(req.created_at),
                    "completed_at": iso(req.completed_at),
                }
            )
    return rows


@app.get("/")
def root():
    return {"status": "ok", "message": "JDE Workflow API", "version": APP_VERSION, "database": "snowflake" if IS_SNOWFLAKE else "sqlite"}


@app.get("/health")
def health():
    return {"status": "ok", "version": APP_VERSION, "database": "snowflake" if IS_SNOWFLAKE else "sqlite"}


@app.get("/api/requests")
def list_requests(db: Session = Depends(get_db)):
    requests = db.query(WorkflowRequest).order_by(WorkflowRequest.created_at.desc()).all()
    return [serialize_request(req, include_steps=True) for req in requests]


@app.post("/api/requests")
def create_request(payload: RequestCreate, db: Session = Depends(get_db)):
    raise_if_validation_errors(validate_create_payload(payload))
    req = build_request_from_payload(db, payload)
    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.get("/api/requests/{request_id}")
def get_request(request_id: int, db: Session = Depends(get_db)):
    return serialize_request(get_request_or_404(db, request_id))


@app.get("/api/requests/{request_id}/audit")
def get_request_audit(request_id: int, db: Session = Depends(get_db)):
    get_request_or_404(db, request_id)
    events = db.query(AuditEvent).filter(AuditEvent.request_id == request_id).order_by(AuditEvent.created_at.asc()).all()
    return [serialize_audit(event) for event in events]


@app.post("/api/requests/{request_id}/submit")
def submit_request(request_id: int, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    if req.status != "DRAFT":
        raise HTTPException(status_code=400, detail="Only draft requests can be submitted.")
    first_step = next_pending_approval(req)
    if not first_step:
        raise HTTPException(status_code=400, detail="No approval steps exist for this request.")
    req.status = "IN_APPROVAL"
    req.submitted_at = now_utc()
    activate_step(req, first_step)
    add_audit(db, req.id, "REQUEST_SUBMITTED", f"REQ-{req.request_number} submitted and routed to {first_step.role_name}.", req.developer_email)
    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.post("/api/requests/{request_id}/steps/{step_id}/action")
def step_action(request_id: int, step_id: int, payload: StepAction, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    step = get_step_or_404(db, request_id, step_id)
    action = str(payload.action or "").strip().upper()
    actor_email = normalize_email(payload.actor_email) or step.assigned_email
    comments = payload.comments or ""

    if req.status in TERMINAL_REQUEST_STATUSES:
        raise HTTPException(status_code=400, detail="Terminal requests cannot be changed.")
    if step.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Only active steps can receive actions.")

    if step.step_type == "APPROVAL":
        if action == "APPROVE":
            step.status = "APPROVED"
            step.response = "APPROVED"
            step.comments = comments
            step.responded_at = now_utc()
            add_audit(db, req.id, "STEP_APPROVED", f"{step.role_name} approved REQ-{req.request_number}.", actor_email)
            advance_after_approval(req)
        elif action == "HOLD":
            step.status = "HOLD"
            step.response = "HOLD"
            step.comments = comments
            step.responded_at = now_utc()
            req.status = "ON_HOLD"
            req.current_stage = "Developer Hold Decision"
            req.hold_step_id = step.id
            decision_step = add_step(db, req, step.sequence_no + 1, "Developer Hold Decision", "DEVELOPER_HOLD_DECISION", req.developer_email, req.developer_name, status="ACTIVE")
            decision_step.started_at = now_utc()
            add_audit(db, req.id, "STEP_HELD", f"{step.role_name} placed REQ-{req.request_number} on hold.", actor_email)
        elif action == "REJECT":
            step.status = "REJECTED"
            step.response = "REJECTED"
            step.comments = comments
            step.responded_at = now_utc()
            req.status = "REJECTED"
            req.current_stage = "Rejected"
            req.completed_at = now_utc()
            add_audit(db, req.id, "REQUEST_REJECTED", f"{step.role_name} rejected REQ-{req.request_number}.", actor_email)
        else:
            raise HTTPException(status_code=400, detail="Approval steps support APPROVE, HOLD, or REJECT.")
    elif step.step_type == "DEVELOPER_HOLD_DECISION":
        if action == "RESUME":
            step.status = "COMPLETED"
            step.response = "RESUME"
            step.comments = comments
            step.responded_at = now_utc()
            held_step = db.query(WorkflowStep).filter(WorkflowStep.id == req.hold_step_id).first() if req.hold_step_id else None
            if held_step:
                held_step.status = "ACTIVE"
                held_step.response = None
                held_step.responded_at = None
                held_step.started_at = now_utc()
                req.status = "IN_APPROVAL"
                req.current_stage = held_step.role_name
            else:
                advance_after_approval(req)
            req.hold_step_id = None
            add_audit(db, req.id, "REQUEST_RESUMED", f"REQ-{req.request_number} resumed from hold.", actor_email)
        elif action == "TERMINATE":
            step.status = "COMPLETED"
            step.response = "TERMINATE"
            step.comments = comments
            step.responded_at = now_utc()
            req.status = "TERMINATED"
            req.current_stage = "Terminated"
            req.completed_at = now_utc()
            add_audit(db, req.id, "REQUEST_TERMINATED", f"REQ-{req.request_number} terminated from hold.", actor_email)
        else:
            raise HTTPException(status_code=400, detail="Hold decision steps support RESUME or TERMINATE.")
    else:
        raise HTTPException(status_code=400, detail="Use CNC completion endpoint for CNC steps.")

    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.post("/api/requests/{request_id}/cancel")
def cancel_request(request_id: int, payload: StepAction, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    if req.status in TERMINAL_REQUEST_STATUSES:
        raise HTTPException(status_code=400, detail="Terminal requests cannot be cancelled.")
    req.status = "CANCELLED"
    req.current_stage = "Cancelled"
    req.completed_at = now_utc()
    for step in req.steps:
        if step.status in {"PENDING", "ACTIVE", "PENDING_CNC"}:
            step.status = "CANCELLED"
    add_audit(db, req.id, "REQUEST_CANCELLED", f"REQ-{req.request_number} cancelled.", normalize_email(payload.actor_email))
    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.post("/api/requests/{request_id}/start-cnc")
def start_cnc(request_id: int, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    if req.status != "APPROVED_PENDING_CNC":
        raise HTTPException(status_code=400, detail="Request must be approved pending CNC before CNC can start.")
    step = next_pending_cnc(req)
    if not step:
        complete_if_cnc_done(req)
    else:
        req.status = "CNC_IN_PROGRESS"
        activate_step(req, step)
        add_audit(db, req.id, "CNC_STARTED", f"CNC started for REQ-{req.request_number} at {step.environment_code}.", step.assigned_email)
    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.post("/api/requests/{request_id}/steps/{step_id}/complete-cnc")
def complete_cnc_step(request_id: int, step_id: int, payload: StepAction, db: Session = Depends(get_db)):
    req = get_request_or_404(db, request_id)
    step = get_step_or_404(db, request_id, step_id)
    actor_email = normalize_email(payload.actor_email) or step.assigned_email
    if step.step_type != "CNC_COMPLETION":
        raise HTTPException(status_code=400, detail="This endpoint only supports CNC completion steps.")
    if step.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Only active CNC steps can be completed.")
    step.status = "COMPLETED"
    step.response = "COMPLETED"
    step.comments = payload.comments or ""
    step.responded_at = now_utc()
    add_audit(db, req.id, "CNC_COMPLETED", f"{step.role_name} completed for REQ-{req.request_number}.", actor_email)
    complete_if_cnc_done(req)
    db.commit()
    db.refresh(req)
    return serialize_request(req)


@app.get("/api/audit/export")
def export_audit_json(db: Session = Depends(get_db)):
    return audit_export_rows(db)


@app.get("/api/audit/export.csv")
def export_audit_csv(db: Session = Depends(get_db)):
    output = StringIO()
    writer = csv.writer(output)
    headers = [
        "Request Number",
        "Jira Ticket",
        "JDE Project",
        "Project Name",
        "Pathcodes",
        "Approver Name",
        "Approver Email",
        "Role",
        "Response",
        "Completed",
        "Emergency Build",
        "Segregation of Duty",
        "Request Status",
        "Created At",
        "Completed At",
    ]
    writer.writerow(headers)
    for row in audit_export_rows(db):
        writer.writerow([row["request_number"], row["jira_ticket"], row["jde_project"], row["project_name"], row["pathcodes"], row["approver_name"], row["approver_email"], row["role"], row["response"], row["completed"], row["emergency_build"], row["segregation_of_duty"], row["request_status"], row["created_at"], row["completed_at"]])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=jde_promotion_approval_history.csv"})
