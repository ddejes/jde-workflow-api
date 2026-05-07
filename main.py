from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="JDE Approval Workflow API", version="0.1.0")


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class RequestStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    IN_APPROVAL = "IN_APPROVAL"
    ON_HOLD = "ON_HOLD"
    REJECTED = "REJECTED"
    TERMINATED = "TERMINATED"
    APPROVED_PENDING_CNC = "APPROVED_PENDING_CNC"
    CNC_IN_PROGRESS = "CNC_IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class StepType(str, Enum):
    APPROVAL = "APPROVAL"
    DEVELOPER_HOLD_DECISION = "DEVELOPER_HOLD_DECISION"
    CNC_COMPLETION = "CNC_COMPLETION"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HOLD = "HOLD"
    RESUMED = "RESUMED"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ActionType(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    HOLD = "HOLD"
    RESUME = "RESUME"
    TERMINATE = "TERMINATE"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"


# -----------------------------------------------------------------------------
# Input models
# -----------------------------------------------------------------------------

class UserRef(BaseModel):
    email: str
    display_name: Optional[str] = None


class RequestCreate(BaseModel):
    jira_project: str
    jira_ticket_url: Optional[str] = None
    omw_project: str
    omw_project_desc: str
    developer: UserRef
    requestor: Optional[UserRef] = None
    business_manager: Optional[UserRef] = None
    technical_manager: UserRef
    project_manager: Optional[UserRef] = None
    peer_review: Optional[UserRef] = None
    cnc: UserRef
    cnc2: UserRef
    emergency: str = "No"
    sox_sod: str = "No"
    special_instructions: Optional[str] = None
    jde_pathcodes: str

    @field_validator("jira_project", "omw_project", "omw_project_desc", "jde_pathcodes")
    @classmethod
    def non_empty(cls, v: str):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class StepActionRequest(BaseModel):
    actor_email: str
    actor_name: Optional[str] = None
    action: ActionType
    comments: Optional[str] = None


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class WorkflowStep(BaseModel):
    id: str
    sequence_no: int
    role_name: str
    assigned_email: str
    status: StepStatus = StepStatus.PENDING


class WorkflowRequest(BaseModel):
    id: str
    request_number: int
    jira_project: str
    omw_project: str
    status: RequestStatus
    current_stage: str
    steps: list[WorkflowStep]


# -----------------------------------------------------------------------------
# In-memory storage
# -----------------------------------------------------------------------------

REQUESTS = {}
NEXT_ID = 1000


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

VALID_PATHCODES = ["DV", "PY", "QA", "PD"]


def parse_pathcodes(raw: str):
    codes = [c.strip().upper() for c in raw.split(",")]
    for c in codes:
        if c not in VALID_PATHCODES:
            raise HTTPException(400, f"Invalid pathcode: {c}")
    return codes


def build_steps(payload: RequestCreate, high: str):
    steps = []
    seq = 1

    def add(role, user):
        nonlocal seq
        steps.append(
            WorkflowStep(
                id=str(uuid4()),
                sequence_no=seq,
                role_name=role,
                assigned_email=user.email
            )
        )
        seq += 1

    if high == "DV":
        add("Technical Manager", payload.technical_manager)

    elif high in ["PY", "QA"]:
        add("Requestor", payload.requestor)
        add("Business Manager", payload.business_manager)
        add("Technical Manager", payload.technical_manager)

    elif high == "PD":
        add("Requestor", payload.requestor)
        add("Business Manager", payload.business_manager)
        add("Peer Reviewer", payload.peer_review)
        add("Technical Manager", payload.technical_manager)
        add("Project Manager", payload.project_manager)

    return steps


# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/requests")
def create_request(payload: RequestCreate):
    global NEXT_ID

    codes = parse_pathcodes(payload.jde_pathcodes)
    high = codes[-1]

    steps = build_steps(payload, high)

    req_id = str(uuid4())

    request = WorkflowRequest(
        id=req_id,
        request_number=NEXT_ID,
        jira_project=payload.jira_project,
        omw_project=payload.omw_project,
        status=RequestStatus.DRAFT,
        current_stage="Draft",
        steps=steps
    )

    REQUESTS[req_id] = request
    NEXT_ID += 1

    return request


@app.post("/api/requests/{request_id}/submit")
def submit(request_id: str):
    req = REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404)

    req.status = RequestStatus.IN_APPROVAL
    first = req.steps[0]
    first.status = StepStatus.ACTIVE
    req.current_stage = first.role_name

    return req


@app.post("/api/requests/{request_id}/steps/{step_id}/action")
def step_action(request_id: str, step_id: str, action: StepActionRequest):
    req = REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404)

    step = next((s for s in req.steps if s.id == step_id), None)
    if not step:
        raise HTTPException(404)

    if action.action == ActionType.APPROVE:
        step.status = StepStatus.APPROVED

        pending = [s for s in req.steps if s.status == StepStatus.PENDING]
        if pending:
            next_step = pending[0]
            next_step.status = StepStatus.ACTIVE
            req.current_stage = next_step.role_name
        else:
            req.status = RequestStatus.COMPLETED
            req.current_stage = "Done"

    elif action.action == ActionType.REJECT:
        step.status = StepStatus.REJECTED
        req.status = RequestStatus.REJECTED
        req.current_stage = "Rejected"

    return req


@app.get("/api/requests")
def list_requests():
    return list(REQUESTS.values())


@app.get("/api/requests/{request_id}")
def get_request(request_id: str):
    return REQUESTS.get(request_id)
